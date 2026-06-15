# ADR 007 — Agent Memory as a Markdown Vault

**Date:** 2026-06-15
**Status:** Accepted

---

## Context

Athena needed a persistent, first-class memory: somewhere the agent can
record durable facts about the user ("prepping for a Meta interview",
"applied to Cloudflare on June 10") and recall them in later, unrelated
conversations. Conversation history already persists in Postgres, but it
is per-conversation and unstructured — there is no way to carry a fact
from one chat into the next, and no way for the user to see or curate
what the assistant "knows."

Phase 14 builds the **substrate and explicit-write path** for that
memory. It deliberately scopes out automatic capture (deferred to Phase
15) and semantic retrieval (embeddings, also deferred). This ADR records
the four structural decisions that shape everything Phase 15 inherits.

---

## Decisions

### 1. A file-based markdown vault, not a database table

Memories live as individual markdown files in a PVC at `/data/memory`,
each with YAML frontmatter (`title`, `created`, `updated`, `tags`) and a
free-text body — an **Obsidian-compatible vault**.

Why files over a `memories` Postgres table:

- **Human-viewable and human-editable.** The whole point of JARVIS-style
  memory is trust: the user can open the vault in Obsidian (or any
  editor), read exactly what Athena believes, fix it, or delete it. A
  DB table is opaque without a custom UI.
- **The format *is* the API.** Frontmatter + markdown is a structure
  Phase 15's automatic capture can parse and update with no schema
  migration — and the same files render in the frontend and in Obsidian
  without translation.
- **Right-sized.** This is a small, personal corpus of discrete notes,
  not high-write relational data. A folder of files matches the shape;
  Postgres would add a query layer we don't need yet. (Same reasoning as
  [ADR 004](004-summary-based-rag.md): match the store to the corpus.)

Tradeoff: no transactional guarantees, no relational queries. Acceptable
at this scale; revisit only if the vault grows large enough that listing
or scanning every file becomes slow.

### 2. Many small notes, not one growing file

Each memory/topic is its own file (`meta-interview-prep.md`,
`cloudflare-application.md`), not one ever-growing `memory.md`.

Why:

- **Update target is unambiguous.** A note's identity is its slugified
  title; a write with the same title updates that file in place. One
  big file would force fragile in-document section editing.
- **Scales and renders.** The frontend `/memory` index lists files; a
  single file would have to be parsed and split on every read.
- **Obsidian-native.** One-note-per-topic is how a vault is meant to be
  used — backlinks, per-note history, individual deletion.

The cost — many files to scan for list/search — is trivial at this scale
and is the same scan Phase 15 would do anyway.

### 3. Explicit capture before automatic capture

Phase 14 writes a memory **only** when the user explicitly says to
("remember that…", "make a note that…", "save this"). The agent never
records anything on its own initiative this phase. The system prompt
states this as a hard rule, and `write_memory` is the only write path.

Why sequence it this way:

- **Correctness of the substrate first.** Get the format, the
  update-vs-duplicate logic, the PVC, and the read/write/UI path proven
  with a human in the loop before letting the agent write unattended.
- **Automatic capture is a *policy* problem, not a plumbing problem.**
  *When* to capture, *what* to capture, and how to avoid noise/PII is a
  design discussion (Phase 15, to be planned in chat). Building the
  policy on top of an already-trusted substrate de-risks it.
- **No mess to inherit.** If automatic capture were built now on top of
  weak dedup, the vault would fill with near-duplicates before the
  policy was right.

### 4. Title-based retrieval before embeddings

Retrieval is plain string matching over note titles, tags, and slugs
(word-level keyword overlap, light stemming) — **no embeddings, no
vector store** this phase.

Why:

- **Sufficient for a small, well-titled vault.** When the user asks
  "what am I prepping for?", a keyword/stem match on titles and tags
  finds the right note. We don't need semantic similarity to
  disambiguate a few dozen notes.
- **No new infrastructure.** Embedding memories would mean a second
  Qdrant collection, an embed-on-write step, and the empty-summary-style
  failure modes that come with making a vector the retrieval key (see
  [ADR 004](004-summary-based-rag.md)). Not worth it until the vault is
  big enough that title matching misses.
- **Cheap to upgrade later.** The note format already carries everything
  an embedding pass would need; adding a vector index later is additive,
  not a rewrite.

Tradeoff: a query whose wording shares no keyword/stem with any note
title or tag won't match. Mitigated by `list_memories` (the agent can
browse the full index and pick) and revisited if recall proves weak.

---

## Alternatives considered

| Option | Rejected because |
| ------ | ---------------- |
| `memories` table in Postgres | Opaque to the user; no Obsidian view; needs a custom UI to inspect/edit; over-engineered for a small note corpus |
| One append-only `memory.md` | Ambiguous update target, fragile in-document editing, must be parsed/split on every read, not Obsidian-idiomatic |
| Build automatic capture now | Capture policy is an unsolved design problem; building it on an unproven substrate risks a duplicate-filled vault and conflates plumbing with policy |
| Embed memories in Qdrant from v1 | Second vector collection + embed-on-write + vector-as-key failure modes, for a corpus small enough that title matching suffices; additive to add later |
| Reuse the documents pipeline (ingest each memory as a document) | Documents are read-only source files with summaries; memories are short, mutable, agent-authored notes — different lifecycle, different update semantics |

---

## Consequences

- **The vault is a product surface, not just storage.** It is openable in
  Obsidian and rendered read-only at `/memory` in the frontend; the user
  can see memory accumulate and (via Obsidian) curate it.
- **`write_memory` owns dedup.** Same-title writes update in place (append
  + bump `updated` + union tags); this is the one piece of real logic and
  is what keeps Phase 15 from inheriting a duplicate-filled vault.
- **The agent and the memory PVC must co-locate.** `local-path` binds the
  PV to the node that first mounts it; the agent is pinned to `xdev-sr`
  (`workload: ai`), so the memory PVC lives on `xdev-sr` — deliberately a
  *different* node from the documents PVC on `vlinux2`.
- **Phase 15 is unblocked and well-defined.** Automatic capture writes
  through the same `write_memory` path and format; only the *trigger
  policy* is new. Embedding-based retrieval can be added later without
  changing the note format.

---

## Related

- [ADR 004 — Summary-based RAG](004-summary-based-rag.md) — same "match the
  store to the corpus shape" reasoning; also the source of the
  vector-as-retrieval-key failure modes this ADR avoids.
- [Phase 14 — Agent Memory](../phases/phase-14-agent-memory.md) — the
  implementation this ADR backs.
- `agent/memory.py` — note format, slugify, and the read/write/search
  helpers.
