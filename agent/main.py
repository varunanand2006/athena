import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg2
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

_executor = ThreadPoolExecutor(max_workers=2)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama.athena.svc.cluster.local:11434")
SEARXNG_BASE_URL = os.getenv("SEARXNG_BASE_URL", "http://searxng.athena.svc.cluster.local:80")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant.athena.svc.cluster.local:6333")
INGESTION_URL = os.getenv("INGESTION_URL", "http://ingestion.athena.svc.cluster.local")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

_PG_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'athena')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'athena')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres.athena.svc.cluster.local')}:5432"
    f"/{os.getenv('POSTGRES_DB', 'athena')}"
)

app = FastAPI(title="Athena Agent")


def get_llm(mode: str):
    if mode == "background":
        return ChatOllama(base_url=OLLAMA_BASE_URL, model=OLLAMA_MODEL, temperature=0)
    return ChatOpenAI(model="gpt-4o-mini", api_key=OPENAI_API_KEY, temperature=0)


def pg_conn():
    return psycopg2.connect(_PG_DSN)


@tool
def web_search(query: str) -> str:
    """Search the web for current information using SearXNG."""
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{SEARXNG_BASE_URL}/search",
            params={"q": query, "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])[:5]
    if not results:
        return "No results found."

    lines = []
    for r in results:
        lines.append(f"- {r.get('title', 'No title')}: {r.get('url', '')}")
        if r.get("content"):
            lines.append(f"  {r['content'][:200]}")
    return "\n".join(lines)


@tool
def find_documents(query: str) -> str:
    """Find which documents are relevant to a query by searching their summaries.
    Returns up to 3 matching documents with their id, title, summary, and similarity score.
    This is the *routing* step — it tells you which documents to read, not the answer.
    After calling this, call load_document with one of the returned ids (or titles) to
    read the full text and answer from real content."""
    with httpx.Client(timeout=30) as client:
        embed_resp = client.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": query},
        )
        embed_resp.raise_for_status()
        vector = embed_resp.json()["embedding"]

        search_resp = client.post(
            f"{QDRANT_URL}/collections/documents/points/search",
            json={"vector": vector, "limit": 3, "with_payload": True},
        )
        search_resp.raise_for_status()
        hits = search_resp.json().get("result", [])

    if not hits:
        return "No matching documents found."

    blocks = []
    for hit in hits:
        payload = hit.get("payload", {})
        title = payload.get("title", "(untitled)")
        doc_id = payload.get("document_id", "")
        summary = (payload.get("summary", "") or "").strip()
        score = hit.get("score", 0)
        blocks.append(f"[score={score:.2f}] {title} (id={doc_id})\n{summary}")
    return "\n\n".join(blocks)


@tool
def load_document(identifier: str) -> str:
    """Load a document's full text from the catalog, given its id (UUID) OR a
    substring of its title/filename. Returns the title and the complete document
    text. Use this AFTER find_documents to read the content needed to answer a
    question — the summary returned by find_documents is for routing only, never
    for substantive answers."""
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title, full_text FROM documents WHERE id::text = %s",
                (identifier,),
            )
            row = cur.fetchone()
            if not row:
                cur.execute(
                    """
                    SELECT title, full_text
                    FROM documents
                    WHERE filename ILIKE %s OR title ILIKE %s
                    ORDER BY added_at DESC
                    LIMIT 1
                    """,
                    (f"%{identifier}%", f"%{identifier}%"),
                )
                row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return f"No document matching '{identifier}' was found."
    title, full_text = row
    if not full_text:
        return f"{title}: (document has no cached full text)"
    return f"{title}\n\n{full_text}"


@tool
def lookup_leetcode(query: str) -> str:
    """Look up LeetCode activity from Postgres. Use for any question about solved problems,
    difficulty breakdown, weekly progress, patterns, or what to focus on next."""
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (p.slug)
                    p.title, p.difficulty, p.solved_at, a.analysis_text
                FROM leetcode_problems p
                LEFT JOIN leetcode_analysis a ON a.problem_slug = p.slug
                ORDER BY p.slug, a.analyzed_at DESC NULLS LAST, p.solved_at DESC
            """)
            all_problems = cur.fetchall()

            cur.execute("""
                SELECT difficulty, COUNT(*) FROM leetcode_problems
                GROUP BY difficulty ORDER BY difficulty
            """)
            breakdown = cur.fetchall()
    finally:
        conn.close()

    if not all_problems:
        return "No LeetCode data in the database yet."

    sorted_problems = sorted(all_problems, key=lambda r: r[2], reverse=True)
    recent = sorted_problems[:15]

    lines = ["=== Difficulty breakdown ==="]
    for diff, count in breakdown:
        lines.append(f"  {diff}: {count}")
    lines.append(f"  Total: {sum(c for _, c in breakdown)}")

    lines.append("\n=== 15 most recently solved ===")
    for title, difficulty, solved_at, analysis in recent:
        lines.append(f"- {title} ({difficulty}) — {solved_at.strftime('%Y-%m-%d')}")
        if analysis:
            lines.append(f"  Analysis: {analysis[:150]}")

    return "\n".join(lines)


@tool
def list_documents() -> str:
    """List every document in the catalog with title, type, added date, and summary.
    Use this for questions like "what documents do you have access to" or "what's
    in my library" — i.e. *browsing* the catalog, not searching content."""
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title, doc_type, added_at, summary FROM documents ORDER BY added_at DESC"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return "No documents in catalog."

    lines = []
    for title, doc_type, added_at, summary in rows:
        added = added_at.strftime("%Y-%m-%d") if added_at else ""
        short = (summary or "").strip().replace("\n", " ")
        if len(short) > 200:
            short = short[:200] + "..."
        lines.append(f"- {title} ({doc_type}, added {added}): {short}")
    return "\n".join(lines)


@tool
def get_table_of_contents() -> str:
    """Return the rendered markdown table of contents for the document library.
    Use this when the user asks to "show the table of contents" or wants the
    catalog as a formatted view."""
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{INGESTION_URL}/toc")
        resp.raise_for_status()
        return resp.text


@tool
def get_document_summary(name: str) -> str:
    """Return the stored one-paragraph summary for a single document, looked up
    by partial match against filename or title. Use this for questions like
    "what's in my resume" or "summarize the project doc"."""
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT title, summary
                FROM documents
                WHERE filename ILIKE %s OR title ILIKE %s
                ORDER BY added_at DESC
                LIMIT 1
                """,
                (f"%{name}%", f"%{name}%"),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return f"No document matching '{name}' was found."
    title, summary = row
    return f"{title}: {summary or '(no summary available)'}"


SYSTEM_PROMPT = (
    "You are Athena, a personal AI assistant. "
    "You have access to these tools: web_search, find_documents, load_document, "
    "lookup_leetcode, list_documents, get_table_of_contents, and get_document_summary. "
    "For content questions about the user's own documents — background, resume, skills, "
    "projects, notes — follow this two-step flow: (1) call find_documents(query) to "
    "identify the relevant document(s) by summary similarity, then (2) call "
    "load_document(id_or_title) on the best match to read its full text, then answer "
    "from that full text. The summary returned by find_documents is for routing only; "
    "never answer substantive content questions from it — always load the full text first. "
    "For questions about which documents exist or what's in the library — e.g. \"what documents "
    "do you have access to\" or \"show me the table of contents\" — use list_documents or "
    "get_table_of_contents to *browse* the catalog (these do not search content). "
    "For \"what's in my <document name>\" or \"summarize my <document name>\", use "
    "get_document_summary with the document's name or filename. "
    "For questions about LeetCode progress, solved problems, difficulty breakdown, "
    "patterns, or what to study next, you MUST call lookup_leetcode before answering. "
    "For questions about current events, job listings, prices, or recent news, "
    "you MUST call web_search before answering. "
    "Never say you cannot access information — use the appropriate tool instead."
)


class ChatRequest(BaseModel):
    message: str
    mode: str = "chat"
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        conn = pg_conn()
        try:
            with conn.cursor() as cur:
                if req.conversation_id is None:
                    title = req.message[:40]
                    cur.execute(
                        "INSERT INTO conversations (title) VALUES (%s) RETURNING id",
                        (title,),
                    )
                    conversation_id = str(cur.fetchone()[0])
                    history = []
                else:
                    conversation_id = req.conversation_id
                    cur.execute(
                        "SELECT role, content FROM messages WHERE conversation_id = %s ORDER BY created_at ASC",
                        (conversation_id,),
                    )
                    history = [{"role": row[0], "content": row[1]} for row in cur.fetchall()]
            conn.commit()
        finally:
            conn.close()

        messages = history + [{"role": "user", "content": req.message}]

        llm = get_llm(req.mode)
        agent = create_react_agent(
            llm,
            tools=[
                web_search,
                find_documents,
                load_document,
                lookup_leetcode,
                list_documents,
                get_table_of_contents,
                get_document_summary,
            ],
            prompt=SYSTEM_PROMPT,
        )
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor,
            lambda: agent.invoke({"messages": messages}),
        )
        last = result["messages"][-1]
        response_text = last.content

        conn = pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO messages (conversation_id, role, content) VALUES (%s, %s, %s)",
                    (conversation_id, "user", req.message),
                )
                cur.execute(
                    "INSERT INTO messages (conversation_id, role, content) VALUES (%s, %s, %s)",
                    (conversation_id, "assistant", response_text),
                )
                cur.execute(
                    "UPDATE conversations SET updated_at = now() WHERE id = %s",
                    (conversation_id,),
                )
            conn.commit()
        finally:
            conn.close()

        return ChatResponse(response=response_text, conversation_id=conversation_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/conversations")
def list_conversations():
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {"id": str(r[0]), "title": r[1], "updated_at": r[2].isoformat()}
        for r in rows
    ]


@app.get("/conversations/{conversation_id}/messages")
def get_conversation_messages(conversation_id: str):
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role, content, created_at FROM messages WHERE conversation_id = %s ORDER BY created_at ASC",
                (conversation_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {"role": r[0], "content": r[1], "created_at": r[2].isoformat()}
        for r in rows
    ]


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/internships")
def internships():
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, company, role, location, priority_score,
                       resume_recommendation, apply_link, found_date
                FROM internship_postings
                WHERE found_date = CURRENT_DATE
                ORDER BY priority_score DESC
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r[0],
            "company": r[1],
            "role": r[2],
            "location": r[3],
            "priority_score": r[4] if r[4] is not None else 0,
            "resume_recommendation": r[5] or "",
            "apply_link": r[6],
            "found_date": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]


@app.get("/leetcode")
def leetcode():
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT difficulty, COUNT(*) FROM leetcode_problems GROUP BY difficulty"
            )
            breakdown = {row[0].lower(): int(row[1]) for row in cur.fetchall()}

            cur.execute("SELECT MAX(solved_at) FROM leetcode_problems")
            last_row = cur.fetchone()
    finally:
        conn.close()

    easy   = breakdown.get("easy", 0)
    medium = breakdown.get("medium", 0)
    hard   = breakdown.get("hard", 0)
    last   = last_row[0].date().isoformat() if last_row and last_row[0] else None

    return {
        "total": easy + medium + hard,
        "easy": easy,
        "medium": medium,
        "hard": hard,
        "last_solved_date": last,
    }


@app.get("/documents")
def list_documents_endpoint():
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, filename, title, doc_type, summary, chunk_count, size_bytes, status, added_at
                FROM documents
                ORDER BY added_at DESC
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "id": str(r[0]),
            "filename": r[1],
            "title": r[2],
            "doc_type": r[3],
            "summary": r[4] or "",
            "chunk_count": r[5],
            "size_bytes": r[6],
            "status": r[7],
            "added_at": r[8].isoformat() if r[8] else None,
        }
        for r in rows
    ]


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# Reuse the env-configured URLs above so a deployment override (e.g. swapping
# Ollama models in dev) is picked up here without a second source of truth.
SYSTEM_HEALTH_CHECKS = [
    ("ingestion", f"{INGESTION_URL}/healthz"),
    ("ollama",    f"{OLLAMA_BASE_URL}/api/tags"),
    ("qdrant",    f"{QDRANT_URL}/healthz"),
    ("searxng",   f"{SEARXNG_BASE_URL}/healthz"),
]


async def _ping_service(client: httpx.AsyncClient, name: str, url: str) -> dict:
    """Treat any non-5xx as reachable. Some services (e.g. SearXNG) may
    return 404 on /healthz but are clearly up — that's still a successful
    network round-trip and a green dot."""
    t0 = time.perf_counter()
    try:
        resp = await client.get(url, timeout=2.0)
        reachable = resp.status_code < 500
        return {
            "name": name,
            "reachable": reachable,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception:
        return {"name": name, "reachable": False, "latency_ms": None}


@app.get("/system/health")
async def system_health():
    """Aggregated reachability + data snapshot for the /system view.

    Self-check is hardcoded reachable=true: if this endpoint responds at
    all, the agent is up. The remaining checks fan out in parallel with
    a 2s per-check timeout so one slow dep can't stall the whole view.
    """
    async with httpx.AsyncClient() as client:
        pinged = await asyncio.gather(
            *[_ping_service(client, name, url) for name, url in SYSTEM_HEALTH_CHECKS]
        )
    services = [{"name": "agent", "reachable": True, "latency_ms": 0}, *pinged]

    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) FROM documents GROUP BY status")
            docs_by_status = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute("SELECT COUNT(*) FROM documents")
            total_docs = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM internship_postings")
            total_internships = cur.fetchone()[0]
            cur.execute("SELECT MAX(found_date) FROM internship_postings")
            last_internship = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM leetcode_problems")
            total_leetcode = cur.fetchone()[0]
            cur.execute("SELECT MAX(solved_at) FROM leetcode_problems")
            last_leetcode = cur.fetchone()[0]
    finally:
        conn.close()

    return {
        "services": services,
        "data": {
            "documents": {
                "total": total_docs,
                "by_status": docs_by_status,
            },
            "internships": {
                "total": total_internships,
                "last_found_date": last_internship.isoformat() if last_internship else None,
            },
            "leetcode": {
                "total_solved": total_leetcode,
                "last_solved_at": last_leetcode.isoformat() if last_leetcode else None,
            },
        },
    }
