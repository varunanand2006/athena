# Phase 16: Memory Recall (Ambient Full-Vault Load)

**Status:** Complete (gates passed)
**Depends on:** Phase 14 (memory vault substrate), Phase 15 (automatic capture)

## Goal

Make the agent actually *use* its memories. Through Phase 15 the vault
captures notes, but the agent only recalls them if it decides to call
`search_memory` — and that retrieval is keyword/title matching, so a question
worded differently from the note silently misses. Phase 16 loads the **whole
vault** into the chat agent's context each turn so the model itself surfaces
relevant memories. Recall becomes the model reasoning over loaded notes — not a
separate retrieval system.

## Why full-vault load and not embeddings (yet)

The Phase 15 doc anticipated Phase 16 would add embeddings. We chose a simpler
mechanism first, because it fits the corpus:

- **The vault is tiny.** A handful of notes fits comfortably in a gpt-4o-mini
  system prompt. Loading everything gives the model strictly more than any
  retriever could surface, with zero retrieval infrastructure.
- **Recall = reasoning, not similarity.** With every note in context, the model
  connects "what big tech stuff am I working toward?" to a note titled "Meta
  interview prep" by *meaning*, something pure title/keyword matching (still in
  place as the `search_memory` tool) cannot do.
- **Embeddings stay deferred, with a named trigger.** A token cap (below) is the
  honest tripwire: when the vault outgrows full-context load, *that* is the
  signal to build embeddings — not a guess made today. (See the cap section.)

This is the same "match the store/mechanism to the corpus shape" reasoning as
[ADR 004](../adr/004-summary-based-rag.md) and
[ADR 007](../adr/007-agent-memory-vault.md).

## Architecture

### `assemble_memory_context()` (agent/memory.py)

The single clean home for "build the block, measure it, decide." It:

1. Reads the entire vault via the existing Phase 14 parse helpers
   (`list_notes` → `read_note`), newest-updated first.
2. Renders each note (title + tags + any events + body) into one block.
3. Measures the block's token count (see token counting).
4. Enforces the cap: if over, trims whole notes from the oldest-updated end so
   we load **up to** the cap rather than overflowing, and flags `over_cap`.
5. Returns `{block, tokens, note_count, max_tokens, over_cap}`. `tokens` is
   always the **full-vault** size (the number to watch as it approaches the
   cap), even when the returned `block` is trimmed.

### System-prompt injection (agent/main.py)

The memory block is injected into the **system prompt**, not as a user-turn
prefix. This is a deliberate boundary decision:

- The memory block is *standing context about the user* — the same category as
  the agent's identity and tool list. It belongs in the system prompt.
- A user-turn prefix would land in the Postgres conversation history (Phase 8)
  and risk the memory blob being saved into stored messages or muddying future
  reflection passes — exactly the cross-contamination class that caused the
  Phase 15 foreground/background bug. Keeping it in the system prompt keeps it
  out of the message record entirely.

`_build_chat_system_prompt()` prepends two **distinct, clearly-labeled**
sections to the base `SYSTEM_PROMPT`:

1. **DATA** — `KNOWN MEMORIES ABOUT THE USER`, the assembled block.
2. **POLICY** — `MEMORY RECALL POLICY` (`RECALL_POLICY`): surface a memory only
   when it genuinely bears on the current turn; never recite/list/summarize
   memories unprompted; ignore the block entirely on unrelated turns; never tell
   the user a memory block exists.

Injection is **chat-path only** (`req.mode == "chat"`, gpt-4o-mini). The
background/reflection path (gemma4:e2b) keeps the bare `SYSTEM_PROMPT` — Phase
16 does not touch reflection or capture.

> **Prompt-enforced boundary caveat** (same class as Phase 15's explicit-only
> rule): the recall policy is enforced by prompt adherence. A different
> foreground model might recite memories unprompted or ignore the block —
> **re-run the Phase 16 gate on any foreground-model swap.**

### Token counting

A cheap heuristic — `~len(chars) // 4` (`_approx_tokens`) — not tiktoken.
Deliberate: this number feeds a tripwire and a /system gauge that are mostly
future-proofing today, so we avoid a tiktoken dependency to sync across
`pyproject.toml` and the Dockerfile. The estimate is plenty accurate for "are we
near the cap?"; swap in tiktoken if exact billing accounting is ever needed.

### The hard-cap tripwire

The project's "explicit tripwire, not silent degradation" pattern. The cap is
`MEMORY_CONTEXT_MAX_TOKENS` (env-overridable, default **8000** — generous vs
today's handful of notes, so it's mostly future-proofing). When the assembled
block exceeds it:

- A clear `WARNING` is logged ("vault too big for full-context load, time for
  embeddings. Loading up to the cap only.").
- `/system/health` reports `over_cap: true`, which the /system view renders as a
  visible ⚠ flag.
- The block is **trimmed to the cap** and still loaded — never overflowed.

This cap is the honest, named trigger for the future embeddings phase. We do
**not** build embeddings now.

### Observability

`/system/health` gains a `data.memory` block: `note_count`, `context_tokens`
(the full-vault token estimate), `max_tokens`, `over_cap`. The chat path also
logs the token count every turn (`Memory context: ~N tokens, M notes (cap K)`)
so the per-turn memory cost (real money on gpt-4o-mini) is watchable, not blind.
The /system view shows a **Memory notes** card with `recall context: ~N / K tok`
and the over-cap warning.

## Implementation Details

### agent/memory.py

- `MEMORY_CONTEXT_MAX_TOKENS` env constant (default 8000).
- `_approx_tokens(text)` — char/4 token estimate.
- `_render_note_for_context(note)` — one note formatted for the prompt block.
- `assemble_memory_context()` — build/measure/cap/return (above).

### agent/main.py

- `RECALL_POLICY` constant — the policy section text.
- `_build_chat_system_prompt()` — assemble DATA + POLICY + base prompt; log
  token count; warn on over_cap; return bare `SYSTEM_PROMPT` for an empty vault.
- `/chat`: `prompt = _build_chat_system_prompt() if req.mode == "chat" else SYSTEM_PROMPT`.
- `/system/health`: add the `data.memory` block from `assemble_memory_context()`.

### frontend/SystemView.tsx

- `data.memory` added to the `SystemHealth` type (optional for backward compat).
- A **Memory notes** card: note count, `~tokens / max tokens`, and an over-cap
  ⚠ line (accent-colored) when the tripwire fires.

## No database / dependency changes

Pure read-over-the-existing-vault. No migration, no new Python dependency
(char/4 heuristic), no Qdrant, no embeddings, no derived index.

## Gate Checklist

1. **Oblique recall works** ✅ — with a note titled "Meta interview prep" in the
   vault, asking in chat "what big tech stuff am I working toward?" (no keyword
   overlap with the note) surfaces it from loaded context — something pure
   title/keyword matching would have missed.
2. **Recall policy holds** ✅ — a turn unrelated to any memory does NOT get
   memories recited at it; the agent answers normally without dumping the block.
3. **Observability** ✅ — /system reports the memory block's token count (and the
   note count), and would flag `over_cap` if the vault exceeded the cap.

## Known Limitations & Future Work

- **Cap is the embeddings trigger.** When `over_cap` starts firing, the vault has
  outgrown full-context load — that is the planned, named moment to build
  embedding-based retrieval (and only load the top-K relevant notes per turn).
- **Per-turn cost grows with the vault.** Every chat turn pays for the loaded
  block on gpt-4o-mini. The /system token gauge is there to watch this.
- **Prompt-enforced recall policy is model-dependent.** Re-verify on any
  foreground-model swap (watch for unprompted recitation of memories).

## Docs

- Previous: [Phase 14](phase-14-agent-memory.md), [Phase 15](phase-15-auto-memory.md)
- [ADR 007](../adr/007-agent-memory-vault.md) — vault substrate
- Next: [Phase 17](phase-17-temporal-memory.md), [ADR 009](../adr/009-temporal-frontmatter.md)
