import asyncio
import os
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
def search_documents(query: str) -> str:
    """Search personal documents (resume, notes, project writeups) stored in Qdrant.
    Use this tool when the question is about the user's own background, skills, experience,
    projects, or anything that would be found in personal documents."""
    with httpx.Client(timeout=30) as client:
        embed_resp = client.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": query},
        )
        embed_resp.raise_for_status()
        vector = embed_resp.json()["embedding"]

        search_resp = client.post(
            f"{QDRANT_URL}/collections/documents/points/search",
            json={"vector": vector, "limit": 5, "with_payload": True},
        )
        search_resp.raise_for_status()
        hits = search_resp.json().get("result", [])

    if not hits:
        return "No relevant documents found."

    lines = []
    for hit in hits:
        text = hit.get("payload", {}).get("text", "").strip()
        score = hit.get("score", 0)
        if text:
            lines.append(f"[score={score:.2f}] {text[:400]}")
    return "\n\n".join(lines) if lines else "No relevant documents found."


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


SYSTEM_PROMPT = (
    "You are Athena, a personal AI assistant. "
    "You have access to three tools: web_search, search_documents, and lookup_leetcode. "
    "For questions about the user's own background, resume, skills, projects, or experience, "
    "you MUST call search_documents before answering. "
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
        agent = create_react_agent(llm, tools=[web_search, search_documents, lookup_leetcode], prompt=SYSTEM_PROMPT)
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


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
