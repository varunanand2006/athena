# Phase 18: Interlinked Memory (the wiki graph)

**Status:** Complete (gate passed)
**Depends on:** Phase 14 (vault), Phase 15 (auto capture), Phase 16 (ambient recall), Phase 17 (events)

## Goal

Stop notes being islands. Synthesis now authors `[[wikilinks]]` between notes,
creates **concept/entity pages**, and **reconciles** existing pages instead of
blind-appending — turning the memory vault into a navigable, compounding wiki
(the Karpathy "LLM Wiki" pattern). The agent already *reads* the whole vault each
turn (Phase 16), so the moment pages link up, that structure is in context for
recall.

This is the **"graph only"** slice: the linking mechanics, built on the existing
conversation-reflection path. Feeding the wiki from the documents library (the
"raw inbox" loop) is the next phase — so in v1 the graph grows from what you
*talk about*, not yet from your document corpus.

## Design (see [ADR 010](../adr/010-interlinked-memory.md))

### The graph is derived from prose, not a second store

`[[wikilinks]]` live in note **bodies** — where synthesis writes them and where
Obsidian renders them natively. Edges are **computed by scanning**, never stored
separately:

- `extract_links(body)` — outgoing links; each `[[Target]]` /
  `[[Target|Display]]` resolves to a target **slug** via the same `slugify()`
  that defines note identity, so `[[Meta interview prep]]` →
  `meta-interview-prep.md`.
- `backlinks(slug)` — incoming links; a full-vault scan (same pattern as Phase
  16's load and Phase 17's events scan).

Same philosophy as `events` (Phase 17): one source of truth, rebuildable by
re-scanning. No graph DB, no link table to drift on edit/delete.

### Synthesis stays on local gemma, and gets graph-aware

Background reflection (`agent/reflection.py`) remains on `gemma4:e2b` — the
foreground=OpenAI / background=local split is unchanged. Its prompt is extended
to:

- **Cross-link** related topics/concepts/people/projects with `[[Exact Title]]`
  inline in the content.
- **Create concept/entity pages** for durable concepts, companies, or people
  central to the conversation, and link them from the notes that mention them.
- **Reconcile** concept pages: a decision with `"concept": true` carries the
  FULL up-to-date page and **replaces** the body; ordinary personal-fact notes
  (`concept` false) keep the Phase 15 **append** behavior.

Conservative capture is intact (durable only, no PII, don't over-link trivia).

> **Synthesis-quality caveat:** cross-linking, deduping concepts, and
> reconciliation are exactly what a small CPU model does least reliably.
> `gemma4:e2b` will produce a sparser, rougher graph than a frontier model
> would; `_sanitize_events`-style tolerance and the slug-resolves-anyway design
> keep bad output cheap, but expect to tune the reflection prompt. This is the
> deliberate cost of keeping background synthesis self-hosted and free.

### Append vs. reconcile, and why rewriting is safe

Reconciling a concept page is a destructive body rewrite. It's made safe by the
**op log**: `append_log()` writes a timestamped line to `_log.md` for every
synthesis write (Karpathy's `log.md`). Combined with the user's delete control
(Phase 15) and the Obsidian-native files, the history of *what changed* survives
even when a page body is overwritten.

### Wiki artifacts

- `_index.md` — the wiki catalog (Karpathy's `index.md`), regenerated after each
  reflection pass via `write_index()`; lists every note with tags / event counts
  and `[[links]]` to each.
- `_log.md` — the append-only operation log.

Both are `_`-prefixed, so the existing `list_notes()` skip rule ignores them —
same convention as the documents' `_TABLE_OF_CONTENTS.md`. They are generated
artifacts, never hand-authored memory.

### Reading the graph

No new traversal *tool* was added: Phase 16 already loads the **whole vault**
(bodies included, so `[[links]]` are visible) into the agent's context each turn,
which at this scale dominates link-following — the model sees every node
simultaneously and can connect them by reasoning. A `related()`/traversal tool
becomes worthwhile only when the vault outgrows full-context load (the same
`over_cap` threshold that triggers embeddings).

### Frontend (`MemoryView.tsx`)

- `[[wikilinks]]` render as **clickable links** that open the target note
  (`linkifyWikilinks` + a custom `note:` href intercepted by the `<a>`
  renderer). A TS `slugify()` mirrors the Python one so targets resolve
  identically. Links to not-yet-written notes render dashed/muted (Obsidian-style
  "broken link").
- A **Linked from** (backlinks) section lists notes that link to the current
  one, each clickable.
- The **graph *view*** is intentionally left to Obsidian ("Obsidian is the IDE")
  — the vault is already Obsidian-native, so opening `/data/memory` gives the
  force-directed graph for free without building a viz.

`GET /memory/{slug}` now returns `links` (outgoing, each with `exists`) and
`backlinks`.

### Graph visualization (its own Library tab)

A third **Graph** tab in `LibraryView` (Documents | Memory Vault | Graph)
renders the whole vault as a force-directed graph:

- Backed by a new `GET /memory/graph` endpoint → `{nodes, edges}`, computed from
  `list_notes` + `extract_links` (undirected, deduped; links to non-existent
  notes dropped so there are no dangling endpoints). Declared **before**
  `/memory/{slug}` so "graph" isn't captured as a slug.
- `GraphView.tsx` is a **dependency-free** SVG force simulation (repulsion +
  edge springs + centering gravity) in `requestAnimationFrame` that cools and
  pauses once settled; interactions reheat it. Nodes are colored by `source`
  (amber=auto, blue=you) and sized by degree; drag a node, scroll to zoom, drag
  the background to pan, hover to highlight a node and its neighbors. No graph
  library — consistent with the project's minimal-dependency ethos (same reason
  the frontmatter parser is hand-rolled and token counting uses char/4). The
  Obsidian graph view remains available too; this just brings a read-only
  version into the app.

### Seed data (`scripts/seed_memory.py`)

A generator that writes ~40 interlinked synthetic wiki notes (one per
topic/project/skill/concept) via the real `memory.write_note`, densely
cross-linked with `[[wikilinks]]`, plus `_index.md`. `source` is `explicit` for
personal-fact/profile notes and `auto` for synthesized concept/entity pages, so
the badge + graph colors show a realistic mix. Used to populate and demo the
graph before reflection has organically grown it. Output is copied into the
agent's `/data/memory` PVC (see handoff). The generated vault is **41 notes / 83
edges, fully connected (no orphans, no broken links)**.

## No database / dependency changes

The graph is derived from markdown bodies. No migration, no new Python
dependency, no embeddings, no Qdrant.

## Gate Checklist

1. **Links authored + navigable** ✅ — a conversation mentioning two related
   concepts → reflection writes notes that `[[link]]` each other (verify in the
   vault / `_log.md`); in `/memory` the link is clickable and opens the target.
2. **Backlinks** ✅ — the target note's **Linked from** section shows the reverse
   edge.
3. **Concept reconcile** ✅ — re-mentioning a concept updates its concept page by
   **replace** (clean rewrite, not an append log), logged in `_log.md`.
4. **Recall over the graph** ✅ — asking the agent about one concept surfaces the
   linked one from loaded context (Phase 16).
5. **Personal-fact notes still append** ✅ — non-concept notes keep their dated
   append trail (Phase 15 behavior unchanged).

## Known Limitations & Future Work

- **Graph grows from conversations only.** Document/raw-inbox → wiki synthesis is
  the next phase (the dense "one source touches many pages" behavior).
- **Link quality is model-bound.** On `gemma4:e2b`, expect sparse/imperfect
  linking; the reflection prompt is the quality knob.
- **No `/lint-wiki` yet** — orphans, broken links, contradiction detection, and
  suggested gaps are a later maintenance phase.
- **No graph-traversal tool** — relying on Phase 16 ambient load until the vault
  outgrows it.
- **Prompt-enforced behaviors** (explicit-only foreground, recall policy, now
  linking discipline) remain model-dependent — re-verify on a model swap.

## Docs

- [ADR 010](../adr/010-interlinked-memory.md) — interlinked-memory design
- Previous: [Phase 16](phase-16-memory-recall.md), [Phase 17](phase-17-temporal-memory.md)
- [ADR 007](../adr/007-agent-memory-vault.md) — vault substrate
