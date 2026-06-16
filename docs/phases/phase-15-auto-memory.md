# Phase 15: Automatic Memory Capture

**Status:** In progress
**Depends on:** Phase 14 (memory vault substrate)

## Goal

Enable the agent to autonomously capture durable memories from conversations without explicit user instruction. Conversations are reflected on at the new-conversation boundary, and a straggler sweep catches any that the boundary trigger missed. The /memory view gains user delete control, making the user the final authority over autonomously-written memories.

## Architecture

### The Watermark Pattern

Two timestamps on `conversations` enable deterministic, idempotent reflection:

- `updated_at` — bumped whenever messages are added (already exists since Phase 8)
- `reflected_at` — set when reflection completes (added in Phase 15 migration)

**DUE definition:** A conversation is due for reflection when:
```sql
reflected_at IS NULL OR updated_at > reflected_at
```

This makes re-reflection automatic: when a user re-opens and extends a conversation, its `updated_at` moves forward, automatically making it due again. No special re-reflection logic needed.

### Reflection Trigger

**New-conversation boundary** (agent/main.py, after `/chat` handler):

1. A new conversation is created (conversation_id was null).
2. Immediately after the chat response is returned, a background thread is spawned.
3. The thread queries all DUE conversations (excluding the brand-new one).
4. For each due conversation, `reflect_on_conversation()` is called (agent/reflection.py).
5. After successful reflection, `reflected_at = now()` is set.

The boundary trigger runs in background threads so it never blocks the chat response.

### The Reflection Pass

**Agent/reflection.py:** `reflect_on_conversation(conversation_id)`

1. **Load full conversation history** from Postgres, ordered by created_at.
2. **Query existing memory index** via `list_memories()` so the reflection knows what already exists.
3. **Send to gemma4:e2b** (background mode, CPU-friendly token limits) with a reflection prompt (see below).
4. **Parse the model's response** (JSON array of memory decisions).
5. **Write memories** via `memory_vault.write_note()` (same function as Phase 14 explicit capture).
   - If a note with the same title (slug) already exists, it updates in place.
   - Tags are merged with the existing note's tags.
6. **Mark reflected:** `UPDATE conversations SET reflected_at = now() WHERE id = ...`

Each conversation's reflection is wrapped in its own try/except so one failure doesn't abort others.

### The Reflection Prompt

Conservative and explicit:

**CAPTURE:**
- Durable facts about the user (what they're working on, prepping for, applied to, struggling with)
- Stated preferences (communication style, tools, workflow choices)
- Project/goal state worth carrying forward

**DO NOT CAPTURE:**
- Transient task content or one-off questions
- Anything already in the memory vault (update existing notes instead)
- Sensitive/PII data: credentials, tokens, financial/health details, anything that looks like a secret
- Trivia, small talk, or things the user didn't treat as significant
- Duplication of external data (documents, LeetCode posts, internship listings)

The prompt explicitly instructs the model to check the vault index before writing, so it updates existing notes instead of creating near-duplicates.

The model outputs a small JSON array of decisions:
```json
[
  {"title": "short topic", "content": "what to remember", "tags": ["tag1", "tag2"], "is_update": true},
  {"title": "another topic", "content": "more content", "tags": [], "is_update": false}
]
```

Or an empty array `[]` if nothing is worth capturing.

### Straggler Sweep (Step 4)

**APScheduler job** in agent/main.py lifespan, runs every 30 minutes:

- Query all DUE conversations.
- Filter to those whose `updated_at` is > 15 minutes ago (avoids reflecting mid-conversation).
- Reflect on each straggler via the same `reflect_on_conversation()` path.

Catches conversations that:
- Ended right before a pod restart (boundary trigger never fired).
- Were somehow missed by the boundary logic (shouldn't happen, but this is a safety net).

### Frontend Delete Control (Step 5)

**/memory view** gains a delete button per note (trash icon in the note header).

**DELETE /memory/{slug}** agent endpoint:
- Verifies the note exists.
- Removes the file from the vault.
- Returns `{"ok": true, "slug": slug}`.

The user can delete any note (explicit or auto-captured), making them the final authority over what's remembered. This is critical now that the agent writes without per-item approval.

## Implementation Details

### agent/reflection.py

New module with:
- `reflect_on_conversation(conversation_id, title)` — the core reflection logic
- `get_due_conversations(exclude_ids)` — query conversations needing reflection
- `_reflection_prompt(messages, memory_index)` — construct the prompt
- `_parse_reflection_response(response_text)` — parse JSON decisions
- Helper functions for loading history and memory index

Token limits tuned for CPU Ollama:
- `num_ctx: 2048`
- `num_predict: 150`
- Full conversation + memory index must fit in ~2000 tokens
- If a conversation is unusually long, it's summarized before reflection (not yet implemented; current behavior overflows)

### agent/main.py

Changes:
- Import reflection module and APScheduler.
- Add `_trigger_reflection_sweep()` to spawn background threads for due conversations.
- Hook it into `/chat` after a new conversation is created.
- Add `_straggler_reflection_sweep()` for the 30-min interval job.
- Wrap both in a lifespan context manager for the scheduler.
- Add `DELETE /memory/{slug}` endpoint.

### agent/pyproject.toml & Dockerfile

Added `apscheduler>=3.10` to both places (as per CLAUDE.md lesson).

### frontend/MemoryView.tsx

Changes:
- Add `deleteNote()` callback that calls `DELETE /memory/{slug}`.
- Confirmation dialog before deletion.
- After successful delete, refresh the notes list and clear selection.
- Add trash button (✕) to the note header.

## Database Migration

**scripts/migrate.sql:**

```sql
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS reflected_at TIMESTAMPTZ DEFAULT NULL;
```

Idempotent. Existing conversations have `reflected_at = NULL`, so they're all initially due for reflection.

## Gate Checklist

The phase is complete when:

1. **Auto-capture works:** Have a conversation mentioning something durable without saying "remember". Start a new conversation. Confirm a memory note gets auto-created (check /memory view and the vault).

2. **Re-reflection updates in place:** Go back to the first conversation, add new messages with additional durable info. Start another new conversation. Confirm the same note is UPDATED in place (not duplicated), with new content, and `reflected_at` advanced.

3. **Conservative policy works:** Have a conversation of pure small talk or a one-off question. Start a new conversation. Confirm NOTHING is written, but the conversation is still marked reflected.

4. **PII exclusion works:** Mention something sensitive (fake API key, financial detail) in passing. Confirm it is NOT auto-captured.

5. **Delete affordance works:** Delete an auto-written note from /memory. Confirm the file is removed from the vault and the vault reloads without it.

## Known Limitations & Future Work

- **Token overflow on very long conversations:** The reflection prompt is sized for ~2000 tokens (CPU Ollama). Extremely long conversations overflow. Mitigation: pass a summarized view of the conversation instead of full history (not yet implemented).
- **No embedding-based retrieval yet:** Memory index is title/keyword only. Phase 16+ will add dense vector retrieval for semantic matching.
- **No automatic recall improvement:** Reflection captures memories, but the agent's recall of those memories in future chats is unchanged (title/tag keyword search). Phase 16 will improve recall with embeddings and automatic search on relevant turns.
- **No `source` field in frontmatter yet:** Phase 15 end-to-end works but the source (explicit vs auto) field recommended in the spec is deferred. Useful for tuning trust but not strictly necessary.

## Docs

- [ADR 008](../adr/008-automatic-memory-capture.md) — design decisions
- Previous: [Phase 14](phase-14-agent-memory.md), [ADR 007](../adr/007-agent-memory-vault.md)
