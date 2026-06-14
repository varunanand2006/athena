# ADR 004 — Summary-Based RAG Replaces Chunk RAG

**Date:** 2026-06-14
**Status:** Accepted

---

## Context

Phase 3 stood up classic chunk RAG: `SimpleDirectoryReader` → `SentenceSplitter`
(512 tokens, 64 overlap) → embed each chunk via `nomic-embed-text` → upsert each
as its own Qdrant point with `document_id` in the payload. The agent's
`search_documents` tool embedded the user's query and returned the top 5
matching chunks.

That architecture is the default for general RAG — and it's a poor fit for this
corpus. Athena's document library is small (tens of documents, not thousands),
the documents are short (class notes, resumes, project writeups — 1 to 20
pages), they're already organized by topic and well-titled, and they contain
no images, scans, or tables that would benefit from cross-chunk reasoning. The
costs of chunking show up clearly under those conditions:

- **Ingestion latency.** Phase 9/10's ingest path embedded every chunk
  sequentially through Ollama on CPU. A short note produced 3–8 chunks, a
  resume 10–15, and each chunk was a round-trip to `nomic-embed-text`.
  Wallclock per ingest was tens of seconds and felt sluggish to a user
  uploading a single file.
- **Fragmented answers.** The top-5 chunks for a query about "what did I cover
  about X in linear algebra" were often the same paragraph repeated with
  overlap, or three chunks from one doc plus two chunks from an unrelated doc
  that happened to share vocabulary. The LLM had to stitch them together with
  no document framing.
- **Failure modes.** A relevant chunk could miss the doc's title, intro, or
  surrounding context — so even when retrieval was "correct" it lacked the
  framing the LLM needed to answer well.
- **Operational complexity.** Each document was 1:N to Qdrant points, so
  re-ingest cleanup required delete-by-filter on `document_id`. Worked, but
  needed careful payload stamping at every upsert site.

The Phase 4 MCP-server work also benefits from a simpler retrieval contract:
"give me a list of relevant documents, then let me read one" composes more
cleanly into tool definitions than "give me semantic snippets."

---

## Decision

Replace chunk RAG with **summary-level routing plus full-document loading**:

1. **Ingest:** extract the full document text, generate a one-paragraph
   `gemma4:e2b` summary, embed *that summary* (not the full text and not any
   chunk), upsert one Qdrant point with payload `{document_id, title,
   summary}`, and cache the full text on the catalog row in a new
   `documents.full_text` column. The summary is now a hard ingestion
   requirement — empty summary marks the row `failed`.
2. **Retrieve:** two agent tools instead of one search tool.
   - `find_documents(query)` — embed the query, search the summary vectors
     (limit=3), return `id / title / summary / score` for each hit. Routing
     only; never the answer.
   - `load_document(id_or_title)` — return `title + full_text` from the
     catalog. This is what the LLM answers from.
3. **System prompt:** explicitly forbid answering substantive content
   questions from the summary alone. Always load the full text first.

The Qdrant `documents` collection schema is unchanged (768-dim cosine), but
its semantics change: one point = one document, not one chunk.

---

## Alternatives considered

| Option | Rejected because |
|--------|-----------------|
| Keep chunk RAG, add a reranker | Doesn't address ingestion cost or fragmented framing — just makes a noisier signal slightly less noisy. Heavier inference stack. |
| Hybrid: chunk vectors + summary vectors in one collection | Doubles ingestion work, two retrieval modes the LLM has to choose between, and the summary path is sufficient for this corpus on its own. |
| BM25 / full-text-search over `documents.full_text` only | Loses semantic recall (paraphrased queries miss exact terms). Acceptable for keyword lookups but bad as the primary surface. |
| Vector-on-chunks, rerank-by-document | Document-aware reranking is what summary routing already approximates — and it requires the chunk infrastructure we want to remove. |
| Per-paragraph instead of per-document | Same fragmentation issue at smaller granularity; doesn't make the retrieval-to-answer flow any cleaner. |

---

## Consequences

**Wins**

- **Cheaper ingestion.** One embed call per document instead of N (typical N
  was 3–15). Single Qdrant upsert. Wallclock per ingest drops to a few
  seconds for short notes — fast enough that the Phase 10 "Processing…" state
  is rarely visible.
- **Higher-quality answers.** The LLM sees an entire document at a time
  rather than 512-token fragments. For short docs this is strictly more
  information than any chunking scheme would surface.
- **Simpler tool semantics.** "Find which doc is relevant" and "read that
  doc" are two clear, separately-named operations. Easier for the LLM to use
  correctly; easier for the upcoming Rust MCP server to expose.
- **1:1 Qdrant↔Postgres mapping.** Re-ingest and row-delete cleanup become
  single-point operations. The same `document_id` filter-delete pattern
  still applies.

**Tradeoffs accepted**

- **Weak on very long documents.** Loading the whole doc means it must fit
  in the LLM context. For Athena's corpus today, every document fits
  comfortably in gpt-4o-mini's window. If a 200-page PDF lands later, we'll
  need to revisit (e.g. fall back to chunked vectors for the few docs over
  some size threshold). This is an accepted tradeoff for now.
- **Summary is now load-bearing.** Pre-Phase-11, a failed summary call was a
  partial success — chunks still answered queries. Now, no summary = nothing
  to embed and nothing to retrieve, so summary failure must mark the row
  `failed`. This makes ingestion slightly more brittle in exchange for a
  guarantee: every `status='complete'` row is retrievable.
- **`chunk_count` column is vestigial.** Kept to avoid a destructive schema
  change. Always 1 on success, 0 while processing. `status` remains the
  source of truth.
- **One-shot retrieval.** Only the top match's full text is typically loaded.
  For a question that genuinely spans two documents, the agent has to call
  `load_document` twice. The LangGraph ReAct loop handles this — it's not a
  blocker — but the model has to recognize the need, which the system prompt
  nudges it toward.

---

## Migration

Phase 11 is a teardown-and-replace, not a hot migration. Existing documents
were test data and were wiped (Qdrant collection DELETEd, `TRUNCATE
documents`, PVC files removed except the auto-skipped `_TABLE_OF_CONTENTS.md`)
before the new ingestion image rolled. The schema migration was a single
idempotent `ALTER TABLE documents ADD COLUMN IF NOT EXISTS full_text TEXT`
that ran cleanly with no backfill. Old rows would have shown up as
`full_text IS NULL` and the agent's `load_document` tool returns a polite
"(document has no cached full text)" message in that case rather than
500-ing — but in practice there were none to backfill.

---

## References

- Phase 3 ADR / phase doc — original chunk RAG decision
- `ingestion/main.py:249` — `_embed_and_summarize` (new summary-routing flow)
- `agent/main.py` — `find_documents`, `load_document`, system prompt
- `scripts/migrate.sql` — Phase 11 ALTER TABLE
- `docs/phases/phase-11-summary-rag.md` — execution notes
