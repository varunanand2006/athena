import asyncio
import os
import re
import time
import threading
import logging
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import httpx
import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from apscheduler.schedulers.background import BackgroundScheduler

import gmail_client
import calendar_client
import memory as memory_vault
import reflection

# Make application INFO logs visible. Under uvicorn, app loggers default to
# WARNING (our reflection logger.info lines would be silently dropped), which
# makes auto-capture impossible to observe. basicConfig adds a root handler at
# INFO so reflection's lifecycle (capturing N memories / created note …) prints.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)
_scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background scheduler on app startup, shut down on exit."""
    _scheduler.add_job(
        _straggler_reflection_sweep,
        "interval",
        minutes=30,
        id="reflection_straggler_sweep",
    )
    _scheduler.start()
    logger.info("Started background scheduler (reflection straggler sweep)")
    try:
        yield
    finally:
        _scheduler.shutdown(wait=False)
        logger.info("Stopped background scheduler")


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama.athena.svc.cluster.local:11434")
SEARXNG_BASE_URL = os.getenv("SEARXNG_BASE_URL", "http://searxng.athena.svc.cluster.local:80")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant.athena.svc.cluster.local:6333")
INGESTION_URL = os.getenv("INGESTION_URL", "http://ingestion.athena.svc.cluster.local")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://mcp-server.athena.svc.cluster.local")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://frontend.athena.svc.cluster.local")

_PG_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'athena')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'athena')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres.athena.svc.cluster.local')}:5432"
    f"/{os.getenv('POSTGRES_DB', 'athena')}"
)

app = FastAPI(title="Athena Agent", lifespan=lifespan)


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


# --- Shared tool implementations -------------------------------------------
# These return structured data and are called by both the @tool wrappers
# (which format to strings for the LLM) and the /tools/* HTTP endpoints
# (which return JSON for the MCP server / other direct callers).


def _find_documents_impl(query: str) -> list[dict]:
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

    results = []
    for hit in hits:
        payload = hit.get("payload", {})
        results.append({
            "document_id": payload.get("document_id", ""),
            "title": payload.get("title", "(untitled)"),
            "summary": (payload.get("summary", "") or "").strip(),
            "score": hit.get("score", 0),
        })
    return results


def _load_document_impl(identifier: str) -> dict | None:
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
        return None
    title, full_text = row
    return {"title": title, "full_text": full_text or ""}


def _lookup_leetcode_impl(
    difficulty: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> dict:
    """Raw structured leetcode data for direct callers (MCP / API).

    With no params: returns recent activity (default 15) + overall breakdown.
    `difficulty` is case-insensitive (easy|medium|hard). `since` is YYYY-MM-DD.
    Topic/pattern filtering is intentionally not done server-side — the caller
    reasons over the returned `analysis` blobs.
    """
    where_clauses = []
    params: list = []
    if difficulty:
        where_clauses.append("LOWER(p.difficulty) = LOWER(%s)")
        params.append(difficulty)
    if since:
        where_clauses.append("p.solved_at >= %s")
        params.append(since)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    effective_limit = limit if (limit is not None and limit > 0) else 15

    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT ON (p.slug)
                    p.title, p.slug, p.difficulty, p.solved_at,
                    a.analysis_text, a.analyzed_at
                FROM leetcode_problems p
                LEFT JOIN leetcode_analysis a ON a.problem_slug = p.slug
                {where_sql}
                ORDER BY p.slug, a.analyzed_at DESC NULLS LAST, p.solved_at DESC
                """,
                params,
            )
            rows = cur.fetchall()

            cur.execute(
                "SELECT LOWER(difficulty), COUNT(*) FROM leetcode_problems GROUP BY LOWER(difficulty)"
            )
            breakdown_rows = cur.fetchall()
    finally:
        conn.close()

    breakdown = {"easy": 0, "medium": 0, "hard": 0}
    for diff, count in breakdown_rows:
        if diff in breakdown:
            breakdown[diff] = int(count)
    breakdown["total"] = breakdown["easy"] + breakdown["medium"] + breakdown["hard"]

    rows_sorted = sorted(rows, key=lambda r: r[3], reverse=True)[:effective_limit]
    problems = []
    for title, slug, diff, solved_at, analysis_text, analyzed_at in rows_sorted:
        problems.append({
            "title": title,
            "slug": slug,
            "difficulty": diff,
            "solved_at": solved_at.isoformat() if solved_at else None,
            "analysis": (
                {
                    "analysis_text": analysis_text,
                    "analyzed_at": analyzed_at.isoformat() if analyzed_at else None,
                }
                if analysis_text else None
            ),
        })

    return {
        "breakdown": breakdown,
        "problems": problems,
        "filters": {
            "difficulty": difficulty,
            "since": since,
            "limit": effective_limit,
        },
    }


# --- LangGraph tool wrappers -----------------------------------------------


@tool
def find_documents(query: str) -> str:
    """Find which documents are relevant to a query by searching their summaries.
    Returns up to 3 matching documents with their id, title, summary, and similarity score.
    This is the *routing* step — it tells you which documents to read, not the answer.
    After calling this, call load_document with one of the returned ids (or titles) to
    read the full text and answer from real content."""
    hits = _find_documents_impl(query)
    if not hits:
        return "No matching documents found."
    blocks = [
        f"[score={h['score']:.2f}] {h['title']} (id={h['document_id']})\n{h['summary']}"
        for h in hits
    ]
    return "\n\n".join(blocks)


@tool
def load_document(identifier: str) -> str:
    """Load a document's full text from the catalog, given its id (UUID) OR a
    substring of its title/filename. Returns the title and the complete document
    text. Use this AFTER find_documents to read the content needed to answer a
    question — the summary returned by find_documents is for routing only, never
    for substantive answers."""
    doc = _load_document_impl(identifier)
    if doc is None:
        return f"No document matching '{identifier}' was found."
    if not doc["full_text"]:
        return f"{doc['title']}: (document has no cached full text)"
    return f"{doc['title']}\n\n{doc['full_text']}"


@tool
def lookup_leetcode(query: str) -> str:
    """Look up LeetCode activity from Postgres. Use for any question about solved problems,
    difficulty breakdown, weekly progress, patterns, or what to focus on next."""
    data = _lookup_leetcode_impl()
    breakdown = data["breakdown"]
    problems = data["problems"]

    if breakdown["total"] == 0:
        return "No LeetCode data in the database yet."

    lines = ["=== Difficulty breakdown ==="]
    for d in ("easy", "medium", "hard"):
        lines.append(f"  {d}: {breakdown[d]}")
    lines.append(f"  Total: {breakdown['total']}")

    lines.append(f"\n=== {len(problems)} most recently solved ===")
    for p in problems:
        solved = (p["solved_at"] or "")[:10]
        lines.append(f"- {p['title']} ({p['difficulty']}) — {solved}")
        if p["analysis"]:
            lines.append(f"  Analysis: {p['analysis']['analysis_text'][:150]}")

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


# --- Memory vault tools (Phase 14) -----------------------------------------
# Explicit capture only: write_memory is called ONLY when the user tells the
# agent to remember something. Retrieval is title/tag string matching, no
# embeddings this phase.


@tool
def write_memory(title: str, content: str, tags: list[str] | None = None) -> str:
    """Save a memory note to the persistent memory vault. Call this ONLY when
    the user explicitly asks you to remember/note/save something. `title` is a
    short topic name (used as the note's identity), `content` is what to
    remember, `tags` is an optional list of short keywords. If a note on the
    same topic (same slugified title) already exists, this UPDATES it in place
    instead of creating a duplicate."""
    result = memory_vault.write_note(title, content, tags or [], source="explicit")
    verb = "Updated existing" if result["action"] == "updated" else "Created"
    tag_str = f" tags={result['tags']}" if result["tags"] else ""
    return (
        f"{verb} memory note '{result['title']}' "
        f"({result['slug']}.md){tag_str}, updated {result['updated']}."
    )


@tool
def list_memories() -> str:
    """List every memory note in the vault with its title, tags, and last
    updated date (newest first). Use this to see what is stored before
    deciding which note to read in full."""
    notes = memory_vault.list_notes()
    if not notes:
        return "The memory vault is empty."
    lines = []
    for n in notes:
        tag_str = f" [{', '.join(n['tags'])}]" if n["tags"] else ""
        lines.append(f"- {n['title']}{tag_str} (updated {n['updated']})")
    return "\n".join(lines)


@tool
def search_memory(query: str) -> str:
    """Search the memory vault for notes relevant to a query and return their
    full content. Use this to recall something the user previously asked you to
    remember. Matching is by title/tag keyword overlap (no embeddings). If
    nothing matches, says so."""
    hits = memory_vault.search_notes(query)
    if not hits:
        return f"No memory notes matched '{query}'."
    blocks = []
    for n in hits:
        tag_str = f" [{', '.join(n['tags'])}]" if n["tags"] else ""
        blocks.append(
            f"### {n['title']}{tag_str} (updated {n['updated']})\n{n['body']}"
        )
    return "\n\n".join(blocks)


# --- Temporal recall tool (Phase 17) ---------------------------------------


def _resolve_window_days(timeframe: str) -> int:
    """Map a free-text timeframe to a forward window in days. Defaults to a
    week so an empty/unknown value still answers 'what's coming up?'."""
    tf = (timeframe or "").lower().strip()
    m = re.search(r"\d+", tf)
    if m:
        return int(m.group())
    if "today" in tf:
        return 0
    if "tomorrow" in tf:
        return 1
    if "month" in tf:
        return 30
    if "year" in tf:
        return 365
    return 7  # "week" / anything else


@tool
def upcoming(timeframe: str = "week") -> str:
    """List upcoming dated events (interviews, deadlines, applications) stored in
    the memory vault, within the given timeframe, sorted by date. Use this for
    any time-based question — "what's coming up this week?", "any deadlines
    soon?", "what's on my calendar?". `timeframe` accepts "today", "tomorrow",
    "week" (default, next 7 days), "month" (next 30 days), or "next N days".
    Each event carries the note title it came from and its kind. This reads
    dates straight from note frontmatter — it is NOT a keyword search."""
    window = _resolve_window_days(timeframe)
    events, note_count, over_cap = memory_vault.collect_events()
    if over_cap:
        # Honest tripwire (parallels Phase 16's cap): a linear full-vault scan
        # has outgrown its welcome — time for a derived index. Not built here.
        logger.warning(
            "upcoming(): vault too big for frontmatter scan (%d notes > cap) — "
            "time for a derived index.",
            note_count,
        )

    today = date.today()
    end = today + timedelta(days=window)
    hits = []
    for ev in events:
        try:
            d = date.fromisoformat(ev["date"])
        except ValueError:
            continue  # malformed date — note still exists as prose
        if today <= d <= end:
            hits.append((d, ev))

    if not hits:
        return f"No upcoming events in the next {window} day(s)."

    hits.sort(key=lambda x: x[0])
    lines = [f"Upcoming events (next {window} day(s)):"]
    for d, ev in hits:
        kind = f" ({ev['kind']})" if ev["kind"] else ""
        lines.append(f"- {d.isoformat()}{kind}: {ev['title']}")
    return "\n".join(lines)


# --- Read-only email lookup (Phase 19) -------------------------------------
# Gmail is an on-demand lookup source like load_document/lookup_leetcode, NOT a
# memory source — it does not feed the vault, reflection, or events this phase.
# The underlying client (gmail_client.py) holds ONLY the gmail.readonly scope;
# there is no send/draft/delete/modify/label path anywhere.


@tool
def search_email(query: str) -> str:
    """Search the user's Gmail inbox READ-ONLY and return a compact digest of
    matching messages (sender, subject, date, short snippet). Use this for
    questions like "did <person/company> email/reply?", "what did the recruiter
    say?", or "find the email about <topic>". Accepts Gmail search syntax —
    e.g. from:stripe, subject:interview, newer_than:7d, "exact phrase". This is
    READ-ONLY: you can search and read email but CANNOT send, draft, reply,
    delete, or label anything. Returns up to 10 messages; answer from them."""
    try:
        messages = gmail_client.search_messages(query, max_results=10)
    except gmail_client.GmailNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Email search failed: {e}"

    if not messages:
        return f"No emails matched '{query}'."

    lines = [f"{len(messages)} email(s) matching '{query}':"]
    for m in messages:
        date = (m["date"] or "")[:31]
        lines.append(f"- From: {m['from']} | {m['subject']} | {date}")
        if m["snippet"]:
            lines.append(f"  {m['snippet']}")
    return "\n".join(lines)


# --- Read-only Google Calendar lookup (Phase 20) ---------------------------
# Calendar is an on-demand lookup source — the agent reaches for it to answer
# schedule questions. Same discipline as search_email: lean digest, read-only.


@tool
def get_calendar_events(timeframe: str) -> str:
    """Look up the user's Google Calendar READ-ONLY and return a compact list of
    events for the requested timeframe. Use this for questions like "what's on
    my schedule today?", "do I have anything this week?", "when is my next
    interview?", "am I free on Friday?". Accepts natural-language timeframes
    such as "today", "tomorrow", "this week", "next 7 days", "next month".
    This is READ-ONLY: you can view events but CANNOT create, edit, or delete
    anything. Returns up to 10 events; answer from them."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    tf = timeframe.lower().strip()
    if tf in ("today",):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif tf in ("tomorrow",):
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif tf in ("this week", "next 7 days", "7 days"):
        start = now
        end = now + timedelta(days=7)
    elif tf in ("next week",):
        start = now + timedelta(days=7)
        end = now + timedelta(days=14)
    elif tf in ("this month", "next 30 days", "30 days"):
        start = now
        end = now + timedelta(days=30)
    else:
        # Default to next 7 days for anything unrecognized.
        start = now
        end = now + timedelta(days=7)

    time_min = start.isoformat()
    time_max = end.isoformat()

    try:
        events = calendar_client.list_events(time_min, time_max, max_results=10)
    except calendar_client.CalendarNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Calendar lookup failed: {e}"

    if not events:
        return f"No events found for '{timeframe}'."

    lines = [f"{len(events)} event(s) for '{timeframe}':"]
    for ev in events:
        lines.append(f"- {ev['start']} → {ev['end']}: {ev['summary']}")
        if ev["location"]:
            lines.append(f"  Location: {ev['location']}")
        if ev["description"]:
            lines.append(f"  {ev['description']}")
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "You are Athena, a personal AI assistant. "
    "You have access to these tools: web_search, find_documents, load_document, "
    "lookup_leetcode, list_documents, get_table_of_contents, get_document_summary, "
    "write_memory, list_memories, search_memory, upcoming, search_email, and "
    "get_calendar_events. "
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
    "MEMORY — read this carefully. The write_memory tool is ONLY for when the "
    "user gives you an EXPLICIT save instruction using words like \"remember "
    "that...\", \"make a note that...\", or \"save this\". In that case you MUST "
    "call write_memory with a short descriptive title and the content. In EVERY "
    "other case you MUST NOT call write_memory. If the user merely mentions a "
    "fact, a goal, an interview, or how they're feeling in passing — e.g. \"I "
    "have a Stripe interview coming up\" or \"I'm trying to hit 500 LeetCode "
    "problems\" — WITHOUT explicitly asking you to save it, do NOT call "
    "write_memory, and do NOT say things like \"I've noted that\" or \"I'll "
    "remember that\". Just respond conversationally. Athena captures durable "
    "facts automatically in the background; foreground saving is NOT your job "
    "and doing it on your own initiative is a mistake. "
    "When a question might be answered by something stored in memory (e.g. "
    "\"what am I prepping for?\", \"what did I apply to?\") and it is NOT already "
    "covered by the loaded memory block at the top of this prompt, call "
    "search_memory (or list_memories first, then search) before answering, and "
    "answer from the note's content. "
    "For TIME-BASED questions about what is coming up — \"what's coming up this "
    "week?\", \"any deadlines soon?\", \"what's on my calendar?\" — you MUST call "
    "upcoming with an appropriate timeframe and answer from the dated events it "
    "returns, rather than guessing from memory text. "
    "For questions about the user's EMAIL — \"did <person/company> reply?\", "
    "\"what did the recruiter say?\", \"find the email about <topic>\", \"any "
    "emails from <sender>?\" — you MUST call search_email (Gmail search syntax "
    "like from:, subject:, newer_than: works) and answer from the matching "
    "messages. Email access is READ-ONLY: you can search and read mail but you "
    "CANNOT send, draft, reply to, delete, or label email — never claim or offer "
    "to do any of those. "
    "For questions about the user's CALENDAR — \"what's on my schedule?\", "
    "\"am I free on <day>?\", \"do I have anything this week?\", \"when is my "
    "next <event>?\" — you MUST call get_calendar_events with the appropriate "
    "timeframe (e.g. \"today\", \"tomorrow\", \"this week\", \"next 7 days\") "
    "and answer from the returned events. Calendar access is READ-ONLY: you can "
    "view events but CANNOT create, edit, or delete anything — never claim or "
    "offer to do any of those. "
    "Never say you cannot access information — use the appropriate tool instead."
)


# --- Ambient memory recall (Phase 16) --------------------------------------
# The whole vault is loaded into the chat agent's system prompt each turn so the
# MODEL surfaces relevant memories — no separate retrieval system. Two distinct,
# clearly-labeled sections are injected: the DATA (the assembled note block) and
# the POLICY (how to use it). Injecting via the system prompt — not a user-turn
# prefix — keeps the memory blob out of the Postgres message record, so it never
# pollutes stored history or future reflection passes (the Phase 15
# foreground/background contamination class).
#
# CAVEAT (re-verify on any foreground-model swap, like Phase 15's explicit-only
# rule): the recall policy below is PROMPT-ENFORCED. A different chat model may
# recite memories unprompted or ignore the block — re-run the Phase 16 gate.

RECALL_POLICY = (
    "--- MEMORY RECALL POLICY ---\n"
    "The 'KNOWN MEMORIES ABOUT THE USER' block above is your standing memory of "
    "this user, reloaded fresh every turn. Treat it as things you already know. "
    "When something in it is relevant to the user's current message, draw on it "
    "naturally — you do NOT need to call search_memory or list_memories for "
    "anything already shown there. Do NOT recite, list, repeat, or summarize "
    "these memories unprompted: surface a memory only when it genuinely bears on "
    "the current turn. If the current message has nothing to do with any stored "
    "memory, ignore the block entirely and respond normally. Never tell the user "
    "that memories were 'loaded' or that you have a memory block.\n"
    "--- END MEMORY RECALL POLICY ---"
)


def _build_chat_system_prompt() -> str:
    """Assemble the chat-path system prompt: the full-vault memory block + recall
    policy prepended to the base prompt. Chat path only (gpt-4o-mini); the
    background/reflection path uses the bare SYSTEM_PROMPT untouched.

    Logs the block's token count every turn so the per-turn memory cost (real
    money on gpt-4o-mini) is observable, and warns loudly when the cap trips."""
    ctx = memory_vault.assemble_memory_context()
    if ctx["over_cap"]:
        logger.warning(
            "Memory context OVER CAP: ~%d tokens > %d cap across %d notes — "
            "vault too big for full-context load, time for embeddings. "
            "Loading up to the cap only.",
            ctx["tokens"], ctx["max_tokens"], ctx["note_count"],
        )
    else:
        logger.info(
            "Memory context: ~%d tokens, %d notes (cap %d).",
            ctx["tokens"], ctx["note_count"], ctx["max_tokens"],
        )

    if not ctx["block"]:
        return SYSTEM_PROMPT  # empty vault — nothing to inject

    data_section = (
        "--- KNOWN MEMORIES ABOUT THE USER (standing context, loaded each turn) ---\n"
        f"{ctx['block']}\n"
        "--- END KNOWN MEMORIES ---"
    )
    return f"{data_section}\n\n{RECALL_POLICY}\n\n{SYSTEM_PROMPT}"


class ChatRequest(BaseModel):
    message: str
    mode: str = "chat"
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str


def _trigger_reflection_sweep(exclude_conversation_id: str):
    """Trigger a reflection sweep on all due conversations (background task).

    Excludes the conversation that just triggered this sweep, since it has no
    content yet. Runs in a background thread so it never blocks the /chat handler.
    """
    try:
        due_convs = reflection.get_due_conversations(exclude_ids=[exclude_conversation_id])
        for conv in due_convs:
            try:
                logger.info(f"Reflecting on conversation {conv['id']}: {conv['title']}")
                reflection.reflect_on_conversation(conv["id"], conv["title"])
            except Exception as e:
                logger.error(f"Reflection failed for {conv['id']}: {e}")
    except Exception as e:
        logger.error(f"Reflection sweep failed: {e}")


def _straggler_reflection_sweep():
    """Periodic job to catch conversations that weren't reflected by the boundary trigger.

    Finds conversations that are DUE for reflection and whose last update was >15 min ago
    (to avoid reflecting mid-conversation). Phase 15 Step 4.
    """
    try:
        # Postgres timestamptz -> timezone-AWARE datetime, so the threshold
        # must be aware too (datetime.utcnow() is naive and would raise
        # TypeError on the comparison below, crashing every sweep).
        threshold = datetime.now(timezone.utc) - timedelta(minutes=15)
        due_convs = reflection.get_due_conversations()

        straggler_count = 0
        for conv in due_convs:
            updated_at = datetime.fromisoformat(conv["updated_at"]) if conv["updated_at"] else None
            if updated_at and updated_at < threshold:
                try:
                    logger.info(f"Straggler sweep: reflecting on {conv['id']} ({conv['title']})")
                    reflection.reflect_on_conversation(conv["id"], conv["title"])
                    straggler_count += 1
                except Exception as e:
                    logger.error(f"Straggler sweep failed for {conv['id']}: {e}")

        if straggler_count > 0:
            logger.info(f"Straggler sweep: reflected {straggler_count} conversations")
    except Exception as e:
        logger.error(f"Straggler sweep failed: {e}")


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        conn = pg_conn()
        is_new_conversation = False
        try:
            with conn.cursor() as cur:
                if req.conversation_id is None:
                    title = req.message[:40]
                    cur.execute(
                        "INSERT INTO conversations (title) VALUES (%s) RETURNING id",
                        (title,),
                    )
                    conversation_id = str(cur.fetchone()[0])
                    is_new_conversation = True
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
        # Phase 16: inject the full-vault memory block + recall policy on the
        # chat path only. The background path keeps the bare prompt.
        prompt = _build_chat_system_prompt() if req.mode == "chat" else SYSTEM_PROMPT
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
                write_memory,
                list_memories,
                search_memory,
                upcoming,
                search_email,
                get_calendar_events,
            ],
            prompt=prompt,
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

        # Trigger reflection sweep on new conversation boundary (Phase 15).
        # Runs in background so it never blocks the response.
        if is_new_conversation:
            thread = threading.Thread(
                target=_trigger_reflection_sweep,
                args=(conversation_id,),
                daemon=True,
            )
            thread.start()

        return ChatResponse(response=response_text, conversation_id=conversation_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE streaming endpoint — emits token, tool_start, tool_end, done events.

    Uses LangGraph's astream_events(version="v2") to iterate over the agent's
    internal events as they happen. The frontend reads these as an EventSource
    and renders tokens word-by-word + shows tool calls in a "thought process" UI.
    """
    import json

    # --- conversation bookkeeping (identical to /chat) ---
    try:
        conn = pg_conn()
        is_new_conversation = False
        try:
            with conn.cursor() as cur:
                if req.conversation_id is None:
                    title = req.message[:40]
                    cur.execute(
                        "INSERT INTO conversations (title) VALUES (%s) RETURNING id",
                        (title,),
                    )
                    conversation_id = str(cur.fetchone()[0])
                    is_new_conversation = True
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
    except Exception as e:
        async def error_gen():
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        return StreamingResponse(error_gen(), media_type="text/event-stream")

    messages = history + [{"role": "user", "content": req.message}]
    llm = get_llm(req.mode)
    prompt = _build_chat_system_prompt() if req.mode == "chat" else SYSTEM_PROMPT
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
            write_memory,
            list_memories,
            search_memory,
            upcoming,
            search_email,
            get_calendar_events,
        ],
        prompt=prompt,
    )

    async def event_generator():
        response_text = ""
        try:
            async for event in agent.astream_events(
                {"messages": messages}, version="v2"
            ):
                kind = event.get("event", "")
                name = event.get("name", "")

                # LLM token streaming
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        token = chunk.content
                        response_text += token
                        yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"

                # Tool call starts
                elif kind == "on_tool_start":
                    tool_input = event.get("data", {}).get("input", {})
                    yield f"event: tool_start\ndata: {json.dumps({'tool': name, 'input': tool_input})}\n\n"

                # Tool call ends
                elif kind == "on_tool_end":
                    tool_output = event.get("data", {}).get("output", "")
                    # Truncate long tool outputs to keep SSE payloads reasonable
                    if isinstance(tool_output, str) and len(tool_output) > 500:
                        tool_output = tool_output[:500] + "..."
                    yield f"event: tool_end\ndata: {json.dumps({'tool': name, 'output': str(tool_output)})}\n\n"

            # Persist messages to Postgres
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

            # Trigger reflection on new-conversation boundary
            if is_new_conversation:
                thread = threading.Thread(
                    target=_trigger_reflection_sweep,
                    args=(conversation_id,),
                    daemon=True,
                )
                thread.start()

            yield f"event: done\ndata: {json.dumps({'response': response_text, 'conversation_id': conversation_id})}\n\n"

        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # tells nginx to not buffer
        },
    )


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


@app.get("/memory")
def memory_index():
    """Frontmatter-only index of the memory vault for the /memory view."""
    return memory_vault.list_notes()


@app.get("/memory/graph")
def memory_graph():
    """Node-link view of the memory wiki for the frontend graph (Phase 18).

    Nodes are notes (slug/title/source/tags + undirected `degree`); edges are
    resolved `[[wikilinks]]` between two EXISTING notes (links to not-yet-written
    notes are dropped so the graph has no dangling endpoints). Computed from the
    same `extract_links` scan the backlinks use. NOTE: this route is declared
    before `/memory/{slug}` so 'graph' isn't captured as a note slug."""
    notes = memory_vault.list_notes()
    by_slug = {n["slug"]: n for n in notes}

    edges = []
    seen_edges = set()
    degree = {n["slug"]: 0 for n in notes}
    for n in notes:
        full = memory_vault.read_note(n["slug"])
        if full is None:
            continue
        for link in memory_vault.extract_links(full["body"]):
            tgt = link["slug"]
            if tgt == n["slug"] or tgt not in by_slug:
                continue
            key = tuple(sorted((n["slug"], tgt)))  # undirected dedup
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({"source": n["slug"], "target": tgt})
            degree[n["slug"]] += 1
            degree[tgt] += 1

    nodes = [
        {
            "slug": n["slug"],
            "title": n["title"],
            "source": n["source"],
            "tags": n["tags"],
            "degree": degree[n["slug"]],
        }
        for n in notes
    ]
    return {"nodes": nodes, "edges": edges}


@app.get("/memory/{slug}")
def memory_note(slug: str):
    """Full content of one memory note (frontmatter fields + markdown body),
    plus the note's graph edges (Phase 18): `links` (outgoing [[wikilinks]] in
    the body, with whether each target note exists yet) and `backlinks` (notes
    that link to this one)."""
    note = memory_vault.read_note(slug)
    if note is None:
        raise HTTPException(status_code=404, detail=f"No memory note '{slug}'.")
    existing_slugs = {n["slug"] for n in memory_vault.list_notes()}
    note["links"] = [
        {**link, "exists": link["slug"] in existing_slugs}
        for link in memory_vault.extract_links(note["body"])
    ]
    note["backlinks"] = memory_vault.backlinks(slug)
    return note


@app.delete("/memory/{slug}")
def delete_memory_note(slug: str):
    """Delete a memory note from the vault by slug."""
    note = memory_vault.read_note(slug)
    if note is None:
        raise HTTPException(status_code=404, detail=f"No memory note '{slug}'.")

    import os
    path = os.path.join(memory_vault.MEMORY_DIR, f"{slug}.md")
    try:
        os.unlink(path)
        return {"ok": True, "slug": slug, "deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete note: {e}")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# --- Direct-call tool endpoints (Phase 12) ---------------------------------
# These BYPASS the LLM reasoning loop and expose existing tool logic as JSON
# for the Rust MCP server (and any other direct caller). All under /tools/
# so a future auth / rate-limit middleware can be applied to the whole class.


class FindDocumentsRequest(BaseModel):
    query: str


@app.post("/tools/find_documents")
def tools_find_documents(req: FindDocumentsRequest):
    try:
        return _find_documents_impl(req.query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LoadDocumentRequest(BaseModel):
    id_or_title: str


@app.post("/tools/load_document")
def tools_load_document(req: LoadDocumentRequest):
    try:
        doc = _load_document_impl(req.id_or_title)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"No document matching '{req.id_or_title}' was found.",
        )
    return doc


class LookupLeetcodeRequest(BaseModel):
    difficulty: str | None = Field(default=None, description="easy | medium | hard")
    since: str | None = Field(default=None, description="YYYY-MM-DD")
    limit: int | None = Field(default=None, ge=1, le=500)


@app.post("/tools/lookup_leetcode")
def tools_lookup_leetcode(req: LookupLeetcodeRequest):
    try:
        return _lookup_leetcode_impl(
            difficulty=req.difficulty,
            since=req.since,
            limit=req.limit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Reuse the env-configured URLs above so a deployment override (e.g. swapping
# Ollama models in dev) is picked up here without a second source of truth.
SYSTEM_HEALTH_CHECKS = [
    ("frontend",  f"{FRONTEND_URL}/"),
    ("ingestion", f"{INGESTION_URL}/healthz"),
    ("mcp-server",f"{MCP_SERVER_URL}/healthz"),
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


async def _ping_postgres() -> dict:
    t0 = time.perf_counter()
    loop = asyncio.get_event_loop()
    def check():
        conn = pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
    
    try:
        # Run DB connection in a thread pool to avoid blocking async loop
        await loop.run_in_executor(_executor, check)
        return {
            "name": "postgres",
            "reachable": True,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception:
        return {"name": "postgres", "reachable": False, "latency_ms": None}


@app.get("/system/health")
async def system_health():
    """Aggregated reachability + data snapshot for the /system view.

    Self-check is hardcoded reachable=true: if this endpoint responds at
    all, the agent is up. The remaining checks fan out in parallel with
    a 2s per-check timeout so one slow dep can't stall the whole view.
    """
    async with httpx.AsyncClient() as client:
        pinged = await asyncio.gather(
            *[_ping_service(client, name, url) for name, url in SYSTEM_HEALTH_CHECKS],
            _ping_postgres()
        )

    services = [{"name": "agent", "reachable": True, "latency_ms": 0}, *pinged]

    # Phase 16: surface the per-turn ambient-memory cost so the approach to the
    # full-context cap is watchable, not blind. over_cap is the named tripwire
    # for the future embeddings phase.
    mem_ctx = memory_vault.assemble_memory_context()

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
            "memory": {
                "note_count": mem_ctx["note_count"],
                "context_tokens": mem_ctx["tokens"],
                "max_tokens": mem_ctx["max_tokens"],
                "over_cap": mem_ctx["over_cap"],
            },
        },
    }
