# Phase 15: Automatic Memory Capture

**Status:** Complete (gates passed 2026-06-15)
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
- `reflect_on_conversation(conversation_id, title)` — the core reflection logic; writes captured notes with `source="auto"` and sets `reflected_at` on success.
- `get_due_conversations(exclude_ids)` — query conversations needing reflection
- `_reflection_prompt(messages, memory_index)` — construct the prompt
- `_parse_reflection_response(response_text)` — parse JSON decisions; resilient to markdown code fences and prose preamble (gemma4:e2b often ignores "JSON only"), falling back to extracting the outermost `[...]` array. A failed parse returns `[]` (capture nothing) rather than raising, so a malformed reflection never crashes the sweep.
- Helper functions for loading history and memory index

Token budget — reflection is BACKGROUND, so unlike foreground chat (which uses `num_ctx: 2048, num_predict: 150` for speed) it trades CPU time for reliability:
- `num_ctx: 4096` — fit the whole short conversation + the existing-memory index
- `num_predict: 512` — emit a COMPLETE JSON array; truncating it would fail the parse and silently capture nothing
- If a conversation is unusually long it can still overflow `num_ctx`; summarize-then-reflect is the planned mitigation (not yet implemented).

### agent/main.py

Changes:
- Import reflection module and APScheduler.
- Add `_trigger_reflection_sweep()` to spawn background threads for due conversations.
- Hook it into `/chat` after a new conversation is created.
- Add `_straggler_reflection_sweep()` for the 30-min interval job (timezone-AWARE threshold — `datetime.now(timezone.utc)`, since Postgres `timestamptz` returns aware datetimes and a naive comparison would crash the sweep).
- Wrap both in a lifespan context manager for the scheduler.
- Add `DELETE /memory/{slug}` endpoint.
- `logging.basicConfig(level=logging.INFO)` so reflection's lifecycle is visible in pod logs (under uvicorn, app loggers default to WARNING and our `logger.info` lines would be silently dropped).
- **Tightened the foreground MEMORY system prompt** (see below).

### Foreground explicit-only prompt (the one non-obvious gotcha)

The foreground chat model (gpt-4o-mini) was over-eager: on a passing mention like "I've got a Stripe interview," it would call `write_memory` itself and reply "I've noted that…" — capturing the fact as `source="explicit"` *before* background reflection could capture it as `source="auto"`. That both mislabels the source and defeats the background-reflection design. The fix is a stricter system prompt: `write_memory` fires ONLY on an explicit "remember/note/save" instruction; for passing mentions the agent must respond conversationally and must NOT claim it saved anything. Background reflection is then the *sole* auto-capturer, which keeps `source="auto"` honest and is what makes gate 1 pass through the real UI. **This prompt is the feature's quality knob** — expect to keep tuning both it and the reflection prompt.

### agent/pyproject.toml & Dockerfile

Added `apscheduler>=3.10` to both places (as per CLAUDE.md lesson). `reflection.py` is `COPY`'d in the Dockerfile alongside `main.py` and `memory.py`.

### agent/memory.py

- Added the `source` frontmatter field (`explicit | auto`). Parse/render/`read_note`/`list_notes` all carry it; missing `source` defaults to `explicit` (any pre-Phase-15 note was, by definition, an explicit write).
- `write_note(..., source=...)` records origin on create and **preserves the existing note's source on update** — origin is a property of the first write, so an auto-reflection touching a user-written note keeps it `explicit` (and vice versa).

### frontend/MemoryView.tsx

Changes:
- Add `deleteNote()` callback that calls `DELETE /memory/{slug}`.
- Confirmation dialog before deletion.
- After successful delete, refresh the notes list and clear selection.
- Add trash button (✕) to the note header.
- `SourceBadge` component — shows **auto** (amber) vs **you** (gray) per note in both the list and the detail header, so the user can audit what the agent captured on its own.

## Database Migration

**scripts/migrate.sql:**

```sql
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS reflected_at TIMESTAMPTZ DEFAULT NULL;
```

Idempotent. Existing conversations have `reflected_at = NULL`, so they're all initially due for reflection.

## Gate Checklist — ALL PASSED (2026-06-15)

1. **Auto-capture works** ✅ — "I've got a Stripe interview coming up… nervous about system design" (no "remember") → starting a new conversation triggered reflection, which created `stripe-interview-prep.md` with `source: auto`. Verified in logs (`capturing 1 memories` → `Created note 'Stripe Interview Prep' [source=auto]`), the vault, and the **auto** badge in /memory.

2. **Re-reflection updates in place** ✅ — adding "the interview got moved to next Friday" to the same conversation and starting a new one re-reflected it; the SAME note was UPDATED (log showed `Updated note`, not a second slug), content appended, `reflected_at` advanced. No duplicate.

3. **Conservative policy works** ✅ — a pure small-talk conversation reflected to `no memories to capture`; nothing written, conversation still marked reflected.

4. **PII exclusion works** ✅ — a passing mention of a fake API key + bank balance was NOT captured to the vault.

5. **Delete affordance works** ✅ — deleting an auto-written note via the /memory trash button removed it from the vault.

Note: passing gate 1 *through the real UI* depended on the foreground explicit-only prompt fix (above). Before that fix, the foreground agent pre-captured the fact as `source: explicit`, which would have failed the "source is auto" check.

## Known Limitations & Future Work

- **Token overflow on very long conversations:** Reflection fits the conversation + memory index into `num_ctx: 4096`. Extremely long conversations still overflow. Mitigation: summarize-then-reflect (not yet implemented).
- **No embedding-based retrieval yet:** Memory index is title/keyword only. Phase 16+ will add dense vector retrieval for semantic matching.
- **No automatic recall improvement:** Reflection captures memories, but the agent's recall of those memories in future chats is unchanged (title/tag keyword search). Phase 16 will improve recall with embeddings and automatic search on relevant turns.
- **Foreground prompt adherence is model-dependent:** the explicit-only rule relies on gpt-4o-mini obeying the system prompt. It's tightened and holds in testing, but a future model swap should re-verify the foreground doesn't auto-save (watch for `source: explicit` notes appearing from passing mentions).

## Docs

- [ADR 008](../adr/008-automatic-memory-capture.md) — design decisions
- Previous: [Phase 14](phase-14-agent-memory.md), [ADR 007](../adr/007-agent-memory-vault.md)
