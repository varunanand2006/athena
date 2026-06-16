# ADR 008: Automatic Memory Capture Policy

**Status:** Accepted
**Date:** 2026-06-15
**Relates to:** Phase 15, ADR 007 (Phase 14 vault substrate)

## Context

Phase 14 established the memory vault substrate: a PVC-backed Obsidian vault with write_memory/list_memories/search_memory tools, all triggered by explicit user instruction ("remember that…"). The agent has no autonomous capture capability yet.

We now want autonomous capture: the agent should reflect on conversations and decide what's worth remembering, without waiting for an explicit "remember" instruction.

The challenge is **capture policy:** what should be captured, when should capture happen, and how do we make re-reflection reliable without duplicating notes or missing conversations?

## Decision

**1. Trigger: New-conversation boundary with watermark (updated_at + reflected_at)**

Trigger reflection at the new-conversation boundary (not idle timeout). Use the watermark pattern:
- `updated_at`: bumped on every message (already exists).
- `reflected_at`: set after successful reflection (new column).
- **Due definition:** `reflected_at IS NULL OR updated_at > reflected_at`.

**Why:**
- Boundary is deterministic: reflection happens exactly once per conversation inception (plus re-opens if extended).
- Watermark is idempotent: no race conditions, no state machine. Re-reflection is automatic for extended conversations.
- Avoids idle timeout ambiguity (what if the user steps away for 5 minutes mid-conversation?).
- Avoids per-turn reflection thrashing (too much LLM load, splits reflections across turn boundaries).

**Alternative considered:** Idle timeout (e.g., reflect if no messages for 15 min). **Rejected** because it's fragile (pod restart could lose the timeout state) and ambiguous (is a 6-minute pause mid-conversation meaningful?).

**Alternative considered:** Per-turn reflection (reflect after every user message). **Rejected** because it's noisy (LLM overhead, memory duplication, splits context across turns), and Phase 16's improved recall will benefit from whole-conversation coherence anyway.

---

**2. Reflection runs in background threads, never blocking chat**

The `/chat` handler spawns a background thread to reflect on due conversations after returning the response.

**Why:**
- Chat latency is user-visible; reflection (to Ollama via slow CPU) is not.
- Reflection can be slow on CPU without hurting UX.
- Failure in reflection doesn't block the user's message.

**Implementation:** `threading.Thread(daemon=True)` in the handler. Per-conversation try/except so one reflection failure doesn't abort the others.

---

**3. Straggler sweep: APScheduler every 30 min**

A background job runs every 30 minutes, finds conversations that are DUE and whose `updated_at` is >15 min old, and reflects on them.

**Why:**
- Catches conversations that ended right before a pod restart (boundary trigger never fires).
- Safety net for the boundary logic (shouldn't happen, but gives us a fallback).
- 30-min interval is reasonable for a background task (not too frequent, not too rare).
- 15-min threshold avoids reflecting mid-conversation (if user's talking, their `updated_at` just moved forward).

---

**4. Whole-conversation reflection + update-in-place**

Reflect on the entire conversation history (all messages, ordered). On re-reflection, re-read earlier turns — don't track deltas.

**Why:**
- Simpler than delta tracking (no need to remember what we already reflected on).
- Robust to pod restarts (full history is always in Postgres).
- Same-slug merge logic (Phase 14) handles update-in-place; re-reflection updates notes without duplicating.
- CPU latency is acceptable (conversation history is usually short).

**Alternative considered:** Delta-based reflection (only reflect on new messages since last reflection). **Rejected** because the Phase 14 same-slug merge logic already handles dedup; delta tracking adds complexity without benefit on typical conversation lengths.

---

**5. Conservative capture policy**

The reflection prompt explicitly captures **only:**
- Durable facts: what user is working on, prepping for, applying to, struggling with.
- Stated preferences: communication style, tools, workflow choices.
- Project/goal state worth carrying forward.

**Explicitly excludes:**
- Transient task content or one-off questions.
- Anything already in the vault (update existing notes instead).
- **PII/secrets:** credentials, tokens, financial/health details, anything that looks like a secret.
- Trivia, small talk, or things the user didn't treat as significant.
- Duplication of external data (documents, LeetCode posts, internship listings).

**Why:**
- Prevents vault pollution (only things that matter long-term).
- Avoids PII in the vault (critical for privacy; once captured to a PVC, it stays).
- Prevents duplication (documents are already searchable; no point storing them again).
- Conservative means low false-positive rate (occasional under-capture is OK; false-positives harm trust).

---

**6. User delete control as authority valve**

The /memory view has a delete button per note. Users can delete any note (explicit or auto-captured).

**Why:**
- The user is the final authority over what's remembered.
- Essential now that the agent captures without per-item approval.
- Gives us visibility into what the agent decided (if users habitually delete certain types, we tune the prompt).
- Combined with a source field (deferred to future work), users can see which notes are auto-captured and audit them.

---

**7. Title/keyword retrieval only; embeddings deferred**

Recall remains title/tag/slug keyword matching. No embeddings in Phase 15.

**Why:**
- Keyword matching works well on a small vault of discrete topics.
- Embeddings add complexity (model hosting, eval time on every search) for marginal UX improvement on small corpora.
- Phase 16 will add embeddings + automatic semantic search on relevant turns; wait until recall becomes the bottleneck.

---

## Consequences

**Positive:**
- Agent autonomously captures durable memories (lower friction than explicit "remember" every time).
- Watermark is idempotent and deterministic (no race conditions, easy to reason about).
- Conservative policy keeps the vault clean and PII-safe.
- User delete control maintains trust and gives us feedback for prompt tuning.

**Negative:**
- Reflection adds LLM latency to the background (not visible but cpu-time-wise); straggler sweep runs every 30 min (minor cluster load).
- Token limits on CPU Ollama mean very long conversations might overflow context (rare, but unhandled currently).
- Keyword-only retrieval means the agent might miss relevant memories on semantic searches (Phase 16 fixes this).

**Trade-offs:**
- Boundary trigger vs idle timeout: we chose simplicity and determinism over flexibility.
- Whole-conversation vs delta reflection: we chose simplicity and robustness over per-turn responsiveness.
- Conservative policy: we chose privacy and clarity over breadth of capture.

## Related Decisions

- **ADR 007** (Phase 14): Vault format, Phase 14's explicit capture, same-slug merge logic.
- **CLAUDE.md:** BackgroundScheduler pattern for async work alongside FastAPI.
- **Phase 16 (future):** Embedding-based retrieval, automatic recall on relevant turns, working continuity.

## Implementation Notes

- `agent/reflection.py`: Core reflection logic; `reflect_on_conversation()`, `get_due_conversations()`, reflection prompt.
- `agent/main.py`: Boundary trigger in `/chat`, straggler sweep in lifespan, DELETE /memory/{slug} endpoint.
- Migration: Add `reflected_at` column to conversations table.
- Frontend: Delete button in /memory view.

See [Phase 15 doc](../phases/phase-15-auto-memory.md) for implementation details and gate checklist.
