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
"""

import logging
import os
import psycopg2
import httpx
from datetime import datetime
from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama.athena.svc.cluster.local:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")

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

    return f"""You are reflecting on a conversation to extract durable memories for the user.

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

OUTPUT FORMAT:
Return ONLY a JSON array of decisions (or an empty array if nothing to capture). Each item has:
{{"title": "short topic name", "content": "what to remember", "tags": ["tag1", "tag2"], "is_update": true/false}}

Example:
[{{"title": "Stripe interview prep", "content": "Interview scheduled for Friday. Focus areas: system design, API design.", "tags": ["interview", "stripe"], "is_update": false}}]

Return ONLY the JSON array, no other text."""


def _parse_reflection_response(response_text: str) -> list[dict]:
    """Parse the model's reflection response into a list of memory decisions."""
    import json
    response_text = response_text.strip()
    if not response_text:
        return []
    try:
        decisions = json.loads(response_text)
        if isinstance(decisions, list):
            return decisions
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse reflection response as JSON: {response_text[:200]}")
    return []


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

        # Send to gemma4:e2b for reflection
        prompt = _reflection_prompt(messages, memory_index)
        llm = ChatOllama(
            base_url=OLLAMA_BASE_URL,
            model=OLLAMA_MODEL,
            temperature=0,
            num_ctx=2048,
            num_predict=150,
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
        for decision in decisions:
            title = decision.get("title", "")
            content = decision.get("content", "")
            tags = decision.get("tags", [])
            if not title or not content:
                continue
            try:
                result = memory_vault.write_note(title, content, tags)
                logger.info(
                    f"  {'Updated' if result['action'] == 'updated' else 'Created'} "
                    f"note '{result['title']}' ({result['slug']}.md)"
                )
            except Exception as e:
                logger.error(f"Failed to write memory '{title}': {e}")

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
