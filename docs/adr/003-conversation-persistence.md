# ADR 003 — Conversation Persistence: Postgres + Stateless Agent

**Date:** 2026-06-11  
**Status:** Accepted

---

## Context
Phase 8 adds multi-chat support. The agent previously ran fully stateless — every `/chat` request was independent, and closing the browser lost all context. To support resumable conversations, we need:

1. A place to store conversation history persistently
2. A way to pass prior context back to the LLM on subsequent turns
3. A UI to list, resume, and delete past conversations

---

## Decision
Store conversations and messages in **PostgreSQL** (already running). Pass history to the agent by loading it from Postgres and prepending it to the `messages` array passed to `create_react_agent`. The agent itself remains stateless — no LangGraph checkpointer, no in-memory session state.

---

## Why Postgres, not a LangGraph checkpointer

LangGraph supports a `PostgresSaver` checkpointer that handles state persistence natively, including tool call traces and intermediate steps. We chose not to use it here for two reasons:

1. **Simplicity** — the `messages` table stores only what the user needs: `role` and `content`. LangGraph checkpoints store the full agent graph state (all intermediate tool calls, reasoning traces) which is overkill for history display and much harder to query.
2. **Frontend requirements** — the conversation list and history view need clean `role/content` pairs. Extracting those from a LangGraph checkpoint blob would require parsing internal state format.

The tradeoff: if a conversation gets very long, loading the full `messages` array increases prompt size on every turn. This is acceptable at current usage levels and can be addressed later with a summarization step if needed.

---

## Why stateless agent per request

`create_react_agent` is constructed fresh on every `/chat` call with the full history injected as the initial `messages`. This means:

- No shared mutable state between requests
- Easy to scale horizontally (any pod handles any request)
- No risk of context bleed between users (future concern)

The downside is that tool calls from prior turns are not visible to the agent as structured tool events — only their text outputs (as assistant messages). This is acceptable because the agent's tools are read-only lookups; prior tool results don't need to be re-evaluated, only their summarized content is relevant.

---

## Schema

```sql
conversations(id UUID PK, title TEXT, created_at, updated_at)
messages(id UUID PK, conversation_id UUID FK, role TEXT, content TEXT, created_at)
INDEX messages(conversation_id)
```

`title` is set once on conversation creation (first 40 chars of the first user message) and never updated. `updated_at` on `conversations` is bumped on every message insertion to drive the sidebar sort order.

---

## Alternatives considered

| Option | Rejected because |
|--------|-----------------|
| LangGraph `PostgresSaver` checkpointer | Stores full graph state — too heavy for simple history display, harder to query for the conversation list |
| Redis / in-memory session | Lost on pod restart; not durable across the power outages this cluster has seen |
| Browser `localStorage` | Not durable across devices or browser clears; no server-side history |
| Dedicated conversation service | Unnecessary extra service for what is two Postgres tables and three endpoints |

---

## Consequences
- Every `/chat` request loads the full conversation history from Postgres before calling the LLM — adds one DB round-trip per request
- Very long conversations increase prompt token count; mitigate later with summarization if needed
- Cascade delete on `messages` keeps cleanup simple — deleting a conversation removes all its messages atomically
