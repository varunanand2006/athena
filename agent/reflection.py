"""Automatic memory capture via reflection on conversation boundaries (Phase 15).

When a conversation ends or is paused, the agent reflects on it autonomously:
(1) Load full conversation history from Postgres.
(2) Query existing memories so reflection knows what already exists.
(3) Send the conversation + memory index to gemma4:e2b with a reflection prompt.
(4) Based on model's decision, write new notes or update existing ones.
(5) Mark the conversation reflected_at = now().

Reflection is conservative: it captures only durable facts, preferences, and
project state. Transient content, PII/secrets, and duplicates of external data
(documents, LeetCode posts) are explicitly excluded.

Phase 21 adds two BACKGROUND external-source sweeps that write into the same
vault via the same conservative policy, tagged with `origin` provenance:

  * reflect_on_calendar()      — fully automatic. The user's calendar is curated
                                 by definition, so upcoming events are swept and
                                 captured as notes/events (origin=calendar).
  * reflect_on_labeled_email() — label-gated. ONLY emails the user hand-labels
                                 (default label "athena") are ingested
                                 (origin=email); the full inbox is NEVER swept.

Both degrade silently if their Google credential isn't mounted, and neither gets
the foreground-only replace/correction capability (append-only, no concept
rewrites) — destructive background rewrites remain out of scope.
"""

import logging
import os
import psycopg2
import httpx
from datetime import date, datetime, timezone
from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama.athena.svc.cluster.local:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")

# --- Phase 21: external-source sweep config --------------------------------
# Calendar: how far forward to sweep, and a min interval between sweeps so the
# LLM doesn't re-run on every single new-conversation boundary (the watermark
# file makes this cheap to enforce). Email: which Gmail label gates ingestion
# (mandatory filter — the full inbox is never swept) and how many to pull.
CALENDAR_SWEEP_WINDOW_DAYS = int(os.getenv("CALENDAR_SWEEP_WINDOW_DAYS", "14"))
CALENDAR_SWEEP_MIN_INTERVAL_HOURS = int(os.getenv("CALENDAR_SWEEP_MIN_INTERVAL_HOURS", "6"))
EMAIL_LABEL = os.getenv("ATHENA_EMAIL_LABEL", "athena")
EMAIL_SWEEP_MAX = int(os.getenv("EMAIL_SWEEP_MAX", "10"))

# Self-contained-in-the-vault watermark for the calendar sweep (Karpathy-style;
# `_`-prefixed so list_notes() skips it). Email uses a Postgres table instead —
# message IDs are structured data, not memory.
_CALENDAR_SWEEP_FILE = "_calendar_sweep.md"

_PG_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'athena')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'athena')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres.athena.svc.cluster.local')}:5432"
    f"/{os.getenv('POSTGRES_DB', 'athena')}"
)


def _pg_conn():
    return psycopg2.connect(_PG_DSN)


def _load_conversation_history(conversation_id: str) -> list[dict]:
    """Load all messages for a conversation, ordered chronologically."""
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role, content, created_at FROM messages WHERE conversation_id = %s ORDER BY created_at ASC",
                (conversation_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    messages = []
    for role, content, created_at in rows:
        messages.append({
            "role": role,
            "content": content,
            "timestamp": created_at.isoformat() if created_at else None,
        })
    return messages


def _get_memory_index() -> str:
    """Load existing memory vault index (frontmatter only) as a string."""
    import memory as memory_vault
    notes = memory_vault.list_notes()
    if not notes:
        return "(Memory vault is empty.)"
    lines = []
    for n in notes:
        tag_str = f" [{', '.join(n['tags'])}]" if n["tags"] else ""
        lines.append(f"- {n['title']}{tag_str} (updated {n['updated']})")
    return "\n".join(lines)


def _reflection_prompt(conversation_history: list[dict], memory_index: str) -> str:
    """Construct the reflection prompt for gemma4:e2b.

    The prompt is conservative: capture only durable facts, preferences, and
    project state. Exclude transient content, PII, and duplication of external data.
    """
    history_text = "\n".join([
        f"[{msg['timestamp']}] {msg['role'].upper()}: {msg['content']}"
        for msg in conversation_history
    ])
    today = date.today().isoformat()

    return f"""You are reflecting on a conversation to extract durable memories for the user.

TODAY'S DATE IS {today}.

CONVERSATION:
{history_text}

EXISTING MEMORY VAULT:
{memory_index}

TASK:
Reflect on this conversation and decide what is worth remembering. Capture ONLY:
- Durable facts about the user (what they're working on, prepping for, applied to, struggling with)
- Stated preferences (communication style, tools, workflow choices)
- Project/goal state worth carrying forward

DO NOT capture:
- Transient task content or one-off questions
- Anything already in the memory vault (update existing notes instead)
- Sensitive/PII data: credentials, tokens, financial/health details, anything that looks like a secret
- Trivia, small talk, or things the user didn't treat as significant
- Duplication of external data (documents, LeetCode posts, internship listings)

Before writing, check the vault index. If a relevant note exists, UPDATE it (same title) rather than creating a near-duplicate.

LINKS & CONCEPT PAGES (build the wiki graph):
This vault is an interlinked wiki, not a pile of isolated notes. When you capture a memory:
- CROSS-LINK related notes using Obsidian wikilink syntax: write [[Exact Note Title]] inline in the content wherever you mention another topic, concept, person, or project that has (or should have) its own note. Use the EXACT title of the target note so the link resolves.
- Create CONCEPT/ENTITY pages: if a durable concept, technology, company, or person is central (e.g. "Distributed systems", "Meta", "LangGraph"), emit a separate decision for it with a short page describing it, and link it from the notes that mention it.
- For a concept/entity page that already exists, RECONCILE it: set "concept": true and put the FULL up-to-date page content in "content" (it REPLACES the page, so include what should remain). For ordinary personal-fact notes leave "concept" false/absent (the content is APPENDED).
- Only link/create pages for things that genuinely recur or matter long-term — do not over-link trivia. Conservative capture still applies.

DATES (events):
If a memory concerns something TIME-BOUND — an interview, a deadline, an application due date, a scheduled event — also record the date(s) in an "events" list. Each event is {{"date": "YYYY-MM-DD", "kind": "<short label like interview|deadline|application>"}}.
- Capture ONLY concrete, resolved calendar dates. Resolve relative dates against TODAY ({today}): e.g. if today is {today} and the user says "next Friday", work out the actual YYYY-MM-DD.
- If the timing is vague or you cannot resolve it to a real date ("sometime soon", "in a few weeks"), DO NOT invent one — leave events empty and keep the timing in the content prose only.
- A memory with no date has "events": [] (most memories).

OUTPUT FORMAT:
Return ONLY a JSON array of decisions (or an empty array if nothing to capture). Each item has:
{{"title": "short topic name", "content": "what to remember, with [[wikilinks]] to related notes", "tags": ["tag1", "tag2"], "events": [{{"date": "YYYY-MM-DD", "kind": "interview"}}], "concept": true/false, "is_update": true/false}}

Example (today is {today}):
[
  {{"title": "Stripe interview prep", "content": "Interview scheduled for next Friday. Prepping [[System design]] and [[API design]]. Role is at [[Stripe]].", "tags": ["interview", "stripe"], "events": [{{"date": "2026-06-19", "kind": "interview"}}], "concept": false, "is_update": false}},
  {{"title": "Stripe", "content": "Payments company. Varun is interviewing here — see [[Stripe interview prep]].", "tags": ["company"], "events": [], "concept": true, "is_update": false}}
]

Return ONLY the JSON array, no other text."""


def _parse_reflection_response(response_text: str) -> list[dict]:
    """Parse the model's reflection response into a list of memory decisions.

    gemma4:e2b often ignores "JSON only" and wraps the array in markdown fences
    or adds a sentence of preamble. We try a strict parse first, then fall back
    to extracting the outermost [...] array from the surrounding noise. A failed
    parse returns [] (capture nothing) rather than raising — a malformed
    reflection must not crash the sweep.
    """
    import json
    response_text = response_text.strip()
    if not response_text:
        return []

    # Strip a leading/trailing markdown code fence if present.
    if response_text.startswith("```"):
        response_text = response_text.strip("`")
        # drop an optional leading "json" language tag
        if response_text.lstrip().lower().startswith("json"):
            response_text = response_text.lstrip()[4:]
        response_text = response_text.strip()

    # Strict parse.
    try:
        decisions = json.loads(response_text)
        if isinstance(decisions, list):
            return decisions
    except json.JSONDecodeError:
        pass

    # Fallback: extract the outermost array from surrounding prose.
    start = response_text.find("[")
    end = response_text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            decisions = json.loads(response_text[start:end + 1])
            if isinstance(decisions, list):
                return decisions
        except json.JSONDecodeError:
            pass

    logger.warning(f"Failed to parse reflection response as JSON: {response_text[:200]}")
    return []


def _sanitize_events(raw) -> list[dict]:
    """Validate model-emitted events. Thin shim over memory.sanitize_events so
    event hygiene has a single source of truth (Phase 21) shared with the
    foreground correction tool and the external-source sweeps."""
    import memory as memory_vault
    return memory_vault.sanitize_events(raw)


def reflect_on_conversation(conversation_id: str, title: str = "") -> bool:
    """Reflect on a single conversation and capture durable memories.

    Returns True if reflection succeeded, False otherwise. Sets reflected_at = now()
    only on success.
    """
    try:
        # Load conversation history
        messages = _load_conversation_history(conversation_id)
        if not messages:
            logger.info(f"Conversation {conversation_id} has no messages, skipping reflection.")
            return True

        # Get existing memory index
        memory_index = _get_memory_index()

        # Send to gemma4:e2b for reflection. Unlike foreground chat, reflection
        # is BACKGROUND (latency doesn't matter) and must emit a COMPLETE JSON
        # array — so we trade CPU time for larger context (fit the whole short
        # conversation + memory index) and output budget (avoid truncating the
        # JSON, which would fail the parse and silently capture nothing).
        prompt = _reflection_prompt(messages, memory_index)
        llm = ChatOllama(
            base_url=OLLAMA_BASE_URL,
            model=OLLAMA_MODEL,
            temperature=0,
            num_ctx=4096,
            num_predict=512,
        )
        result = llm.invoke(prompt)
        response_text = result.content

        # Parse decisions
        decisions = _parse_reflection_response(response_text)
        if not decisions:
            logger.info(f"Conversation {conversation_id}: no memories to capture.")
        else:
            logger.info(f"Conversation {conversation_id}: capturing {len(decisions)} memories.")

        # Write memories
        import memory as memory_vault
        wrote_any = False
        for decision in decisions:
            title = decision.get("title", "")
            content = decision.get("content", "")
            tags = decision.get("tags", [])
            events = _sanitize_events(decision.get("events", []))
            # Concept/entity pages are RECONCILED (clean rewrite); ordinary
            # personal-fact notes are APPENDED (Phase 15 behavior). Phase 18.
            is_concept = bool(decision.get("concept", False))
            if not title or not content:
                continue
            try:
                result = memory_vault.write_note(
                    title, content, tags, source="auto", events=events,
                    replace=is_concept,
                )
                wrote_any = True
                logger.info(
                    f"  {result['action'].capitalize()} note '{result['title']}' "
                    f"({result['slug']}.md) [source={result['source']}"
                    f"{', concept' if is_concept else ''}]"
                )
                # Op log is the audit trail that makes reconcile (rewrite) safe.
                memory_vault.append_log(
                    f"{result['action']} {'concept ' if is_concept else ''}"
                    f"note [[{result['title']}]] from conversation {conversation_id}"
                )
            except Exception as e:
                logger.error(f"Failed to write memory '{title}': {e}")

        # Regenerate the wiki catalog once, after all writes for this pass.
        if wrote_any:
            try:
                memory_vault.write_index()
            except Exception as e:
                logger.error(f"Failed to regenerate memory index: {e}")

        # Mark conversation as reflected
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE conversations SET reflected_at = now() WHERE id = %s",
                    (conversation_id,),
                )
            conn.commit()
        finally:
            conn.close()

        return True

    except Exception as e:
        logger.error(f"Reflection failed for conversation {conversation_id}: {e}")
        return False


def get_due_conversations(exclude_ids: list[str] | None = None) -> list[dict]:
    """Query conversations that are DUE for reflection.

    A conversation is due when reflected_at IS NULL OR updated_at > reflected_at.
    """
    exclude_ids = exclude_ids or []

    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(exclude_ids)) if exclude_ids else ""
            where = "WHERE (reflected_at IS NULL OR updated_at > reflected_at)"
            if exclude_ids:
                where += f" AND id NOT IN ({placeholders})"

            query = f"""
                SELECT id, title, created_at, updated_at, reflected_at
                FROM conversations
                {where}
                ORDER BY updated_at DESC
            """
            cur.execute(query, exclude_ids if exclude_ids else [])
            rows = cur.fetchall()
    finally:
        conn.close()

    conversations = []
    for conv_id, title, created_at, updated_at, reflected_at in rows:
        conversations.append({
            "id": str(conv_id),
            "title": title,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "reflected_at": reflected_at.isoformat() if reflected_at else None,
        })
    return conversations


# === Phase 21: external-source feeds (calendar + labeled email) =============
# Both write into the same vault as conversation reflection, through the same
# conservative-capture policy, but tagged with an `origin` so the user can audit
# (and delete) feed-captured notes. Both degrade SILENTLY if the relevant Google
# credential isn't mounted — the agent runs fine without either.


def _apply_feed_decisions(decisions: list[dict], origin: str, log_source: str) -> bool:
    """Write a list of reflection decisions into the vault as APPEND-ONLY notes
    tagged `source=auto, origin=<origin>`.

    Shared by the calendar and email sweeps. Deliberately append-only: feeds do
    NOT get the foreground replace/correction capability, and they don't author
    concept-page rewrites — keeping unattended gemma writes non-destructive (the
    Phase 18/21 background-safety boundary). Returns whether anything was written.
    """
    import memory as memory_vault

    if not decisions:
        logger.info(f"{log_source}: no memories to capture.")
        return False

    wrote_any = False
    for decision in decisions:
        title = decision.get("title", "")
        content = decision.get("content", "")
        tags = decision.get("tags", [])
        events = memory_vault.sanitize_events(decision.get("events", []))
        if not title or not content:
            continue
        try:
            result = memory_vault.write_note(
                title, content, tags, source="auto", events=events, origin=origin,
            )
            wrote_any = True
            logger.info(
                f"  {result['action'].capitalize()} note '{result['title']}' "
                f"({result['slug']}.md) [source=auto, origin={origin}]"
            )
            memory_vault.append_log(
                f"{result['action']} note [[{result['title']}]] from {log_source}"
            )
        except Exception as e:
            logger.error(f"Failed to write memory '{title}': {e}")

    if wrote_any:
        try:
            memory_vault.write_index()
        except Exception as e:
            logger.error(f"Failed to regenerate memory index: {e}")
    return wrote_any


# --- Calendar sweep watermark (a self-contained file in the vault) ----------


def _sweep_marker_path(filename: str) -> str:
    import memory as memory_vault
    return os.path.join(memory_vault.MEMORY_DIR, filename)


def _read_sweep_marker(filename: str):
    """Return the last-run datetime stored in a `_*_sweep.md` marker, or None."""
    path = _sweep_marker_path(filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("last_run:"):
                    return datetime.fromisoformat(line.split(":", 1)[1].strip())
    except Exception as e:
        logger.warning(f"Could not read sweep marker {filename}: {e}")
    return None


def _write_sweep_marker(filename: str, when: datetime) -> None:
    """Atomically record the last-run timestamp for a sweep."""
    import memory as memory_vault
    os.makedirs(memory_vault.MEMORY_DIR, exist_ok=True)
    path = _sweep_marker_path(filename)
    text = (
        "# Calendar sweep watermark\n\n"
        "Generated by Phase 21 reflection — records the last calendar sweep so it\n"
        "is not re-run on every new-conversation boundary.\n\n"
        f"last_run: {when.isoformat()}\n"
    )
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _calendar_reflection_prompt(events: list[dict], memory_index: str) -> str:
    today = date.today().isoformat()
    lines = []
    for ev in events:
        loc = f" @ {ev['location']}" if ev.get("location") else ""
        desc = f" — {ev['description']}" if ev.get("description") else ""
        lines.append(f"- {ev.get('start','')} → {ev.get('end','')}: {ev.get('summary','')}{loc}{desc}")
    events_text = "\n".join(lines)

    return f"""You are reflecting on the user's UPCOMING CALENDAR EVENTS to capture durable memories.

TODAY'S DATE IS {today}.

UPCOMING CALENDAR EVENTS (next {CALENDAR_SWEEP_WINDOW_DAYS} days):
{events_text}

EXISTING MEMORY VAULT:
{memory_index}

TASK:
The user deliberately put these events on their calendar, so they are curated and worth attention. Decide which warrant a durable memory note or an events-frontmatter update on an EXISTING relevant note. Capture ONLY things worth remembering long-term:
- Interviews, deadlines, application due dates, and meetings tied to the user's goals/projects.
- Routine personal noise with no durable signal (e.g. "lunch", "gym", "commute") should be SKIPPED unless clearly significant.

For each captured event:
- Record the date in an "events" list: {{"date": "YYYY-MM-DD", "kind": "<interview|deadline|meeting|...>"}}. Use the event's real calendar date (resolve relatives against TODAY {today}).
- If a relevant note already exists (check the vault index), UPDATE it (same title) instead of creating a near-duplicate.
- Cross-link related notes with [[Exact Note Title]] inline where natural.

DO NOT capture:
- Sensitive/PII data: credentials, tokens, financial/health details, anything secret-looking.
- Trivia or routine events with no durable significance.

OUTPUT FORMAT:
Return ONLY a JSON array of decisions (or an empty array [] if nothing is worth capturing). Each item:
{{"title": "short topic name", "content": "what to remember, with [[wikilinks]]", "tags": ["tag1"], "events": [{{"date": "YYYY-MM-DD", "kind": "interview"}}]}}

Return ONLY the JSON array, no other text."""


def reflect_on_calendar() -> bool:
    """Sweep upcoming Google Calendar events into the memory vault (Phase 21).

    Fully automatic: the calendar is user-curated, so upcoming events (next
    CALENDAR_SWEEP_WINDOW_DAYS) are fetched and gemma decides which warrant a
    durable note/events update — written append-only with source=auto,
    origin=calendar. Throttled by a last-run watermark so it doesn't re-run the
    LLM on every conversation boundary. Returns True on success/skip, False on a
    real failure. Degrades silently if the gcal-secret isn't mounted.
    """
    import calendar_client
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    last = _read_sweep_marker(_CALENDAR_SWEEP_FILE)
    if last is not None and (now - last) < timedelta(hours=CALENDAR_SWEEP_MIN_INTERVAL_HOURS):
        logger.info(
            "Calendar sweep skipped (last run %s, within %dh throttle).",
            last.isoformat(), CALENDAR_SWEEP_MIN_INTERVAL_HOURS,
        )
        return True

    time_min = now.isoformat()
    time_max = (now + timedelta(days=CALENDAR_SWEEP_WINDOW_DAYS)).isoformat()
    try:
        events = calendar_client.list_events(time_min, time_max, max_results=25)
    except calendar_client.CalendarNotConfigured:
        logger.info("Calendar sweep skipped — gcal-secret not configured.")
        return True
    except Exception as e:
        logger.error(f"Calendar sweep: failed to fetch events: {e}")
        return False

    if not events:
        logger.info("Calendar sweep: no upcoming events in the next %d days.", CALENDAR_SWEEP_WINDOW_DAYS)
        _write_sweep_marker(_CALENDAR_SWEEP_FILE, now)
        return True

    memory_index = _get_memory_index()
    prompt = _calendar_reflection_prompt(events, memory_index)
    llm = ChatOllama(
        base_url=OLLAMA_BASE_URL, model=OLLAMA_MODEL,
        temperature=0, num_ctx=4096, num_predict=512,
    )
    try:
        result = llm.invoke(prompt)
        decisions = _parse_reflection_response(result.content)
    except Exception as e:
        logger.error(f"Calendar sweep: reflection LLM failed: {e}")
        return False

    logger.info("Calendar sweep: %d event(s) considered, %d decision(s).", len(events), len(decisions))
    _apply_feed_decisions(decisions, origin="calendar", log_source="calendar sweep")
    _write_sweep_marker(_CALENDAR_SWEEP_FILE, now)
    return True


# --- Email sweep: processed-message-ID ledger (Postgres) --------------------
# Message IDs are structured data, not memory, so they live in Postgres (cleaner
# than a vault file). The table is created via migrate.sql; we also ensure it
# defensively so a missed migration doesn't silently break the sweep.


def _ensure_email_processed_table() -> None:
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS email_processed (
                    message_id   TEXT PRIMARY KEY,
                    label        TEXT,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def _filter_unprocessed(message_ids: list[str]) -> set[str]:
    """Return the subset of message_ids NOT already in email_processed."""
    if not message_ids:
        return set()
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(message_ids))
            cur.execute(
                f"SELECT message_id FROM email_processed WHERE message_id IN ({placeholders})",
                message_ids,
            )
            seen = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    return set(message_ids) - seen


def _mark_email_processed(message_id: str, label: str) -> None:
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO email_processed (message_id, label) VALUES (%s, %s) "
                "ON CONFLICT (message_id) DO NOTHING",
                (message_id, label),
            )
        conn.commit()
    finally:
        conn.close()


def _email_reflection_prompt(messages: list[dict], memory_index: str) -> str:
    today = date.today().isoformat()
    blocks = []
    for m in messages:
        body = (m.get("body") or m.get("snippet") or "").strip()[:1500]
        blocks.append(
            f"From: {m.get('from','')}\n"
            f"Subject: {m.get('subject','')}\n"
            f"Date: {m.get('date','')}\n"
            f"{body}"
        )
    emails_text = "\n\n---\n\n".join(blocks)

    return f"""You are reflecting on EMAILS the user explicitly LABELED "{EMAIL_LABEL}" for memory, to capture durable facts.

TODAY'S DATE IS {today}.

LABELED EMAILS:
{emails_text}

EXISTING MEMORY VAULT:
{memory_index}

TASK:
The user hand-labeled these emails as worth remembering, so they are curated. Extract durable facts worth carrying forward:
- Application/interview status, offers, deadlines, commitments, important contacts or decisions tied to the user's goals.
If a relevant note already exists (check the vault index), UPDATE it (same title) rather than duplicating. Cross-link with [[Exact Note Title]] inline where natural.

If an email mentions a TIME-BOUND thing (interview, deadline, due date), record it in an "events" list: {{"date": "YYYY-MM-DD", "kind": "<interview|deadline|...>"}} — resolve relatives against TODAY {today}. Only concrete, resolvable dates; otherwise leave events empty and keep the timing in prose.

DO NOT capture:
- Sensitive/PII: credentials, tokens, full financial/health details, anything secret-looking. A labeled email may still contain noise — be conservative.
- Marketing/transient content with no durable significance.
- Anything already in the vault (update it instead).

OUTPUT FORMAT:
Return ONLY a JSON array of decisions (or an empty array [] if nothing is worth capturing). Each item:
{{"title": "short topic name", "content": "what to remember, with [[wikilinks]]", "tags": ["tag1"], "events": [{{"date": "YYYY-MM-DD", "kind": "interview"}}]}}

Return ONLY the JSON array, no other text."""


def reflect_on_labeled_email() -> bool:
    """Sweep label-filtered Gmail into the memory vault (Phase 21).

    ONLY emails carrying the configured Gmail label (default "athena") are
    ingested — the full inbox is NEVER swept; the user stays the curator. Each
    not-yet-processed labeled email is fed to gemma, which decides what durable
    fact to capture (append-only, source=auto, origin=email). The message ID is
    then marked processed in Postgres so it is not re-captured on the next sweep,
    even if it produced no memory. Degrades silently if the gmail-secret isn't
    mounted. Returns True on success/skip, False on a real failure.
    """
    import gmail_client

    try:
        _ensure_email_processed_table()
    except Exception as e:
        logger.error(f"Email sweep: could not ensure email_processed table: {e}")
        return False

    query = f"label:{EMAIL_LABEL}"
    try:
        messages = gmail_client.search_messages(query, max_results=EMAIL_SWEEP_MAX)
    except gmail_client.GmailNotConfigured:
        logger.info("Email sweep skipped — gmail-secret not configured.")
        return True
    except Exception as e:
        logger.error(f"Email sweep: search failed: {e}")
        return False

    if not messages:
        logger.info("Email sweep: no emails carry label '%s'.", EMAIL_LABEL)
        return True

    ids = [m["id"] for m in messages]
    unprocessed = _filter_unprocessed(ids)
    new_messages = [m for m in messages if m["id"] in unprocessed]
    if not new_messages:
        logger.info("Email sweep: all %d labeled email(s) already processed.", len(messages))
        return True

    # The search digest gives only a snippet; pull fuller plain-text bodies for
    # the unprocessed ones so the model has enough to extract a durable fact.
    enriched = []
    for m in new_messages:
        body = ""
        try:
            full = gmail_client.get_message(m["id"])
            body = full.get("body", "")
        except Exception as e:
            logger.warning(f"Email sweep: could not fetch body for {m['id']}: {e}")
        enriched.append({**m, "body": body})

    memory_index = _get_memory_index()
    prompt = _email_reflection_prompt(enriched, memory_index)
    llm = ChatOllama(
        base_url=OLLAMA_BASE_URL, model=OLLAMA_MODEL,
        temperature=0, num_ctx=4096, num_predict=512,
    )
    try:
        result = llm.invoke(prompt)
        decisions = _parse_reflection_response(result.content)
    except Exception as e:
        logger.error(f"Email sweep: reflection LLM failed: {e}")
        return False

    logger.info("Email sweep: %d new labeled email(s), %d decision(s).", len(new_messages), len(decisions))
    _apply_feed_decisions(decisions, origin="email", log_source=f"email label:{EMAIL_LABEL}")

    # Mark EVERY considered message processed — even ones that produced no
    # memory — so "considered and skipped" is not re-evaluated every sweep. The
    # op log + user delete control are the recovery valves if capture was wrong.
    for m in new_messages:
        try:
            _mark_email_processed(m["id"], EMAIL_LABEL)
        except Exception as e:
            logger.error(f"Email sweep: failed to mark {m['id']} processed: {e}")
    return True
