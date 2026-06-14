# Phase 11 — Summary-Based RAG

## Goal
Replace chunk-level RAG with summary-level routing + full-document loading.
The corpus is small, short, and well-organized (class notes, resumes, project
writeups), so chunking adds ingestion cost and fragments answers without
buying recall worth the cost. New model:

- **Ingestion** produces one Qdrant point per document — its vector is the
  embedding of the gemma4:e2b summary, not of any chunk. Full extracted text
  is cached in a new `documents.full_text` Postgres column.
- **Retrieval** is two tools: `find_documents(query)` searches the summary
  vectors (limit 3), `load_document(id_or_title)` returns the full text from
  Postgres. The agent answers from the full text, never from the summary.

The old `search_documents` tool is removed. The summary is now a *required*
ingestion artifact — empty summary marks the row `failed`. See
[ADR 004](../adr/004-summary-based-rag.md) for the decision rationale and
tradeoffs.

## Phase gate
1. Upload 2–3 short notes on distinct topics through the Documents UI. Each
   row goes `processing → complete` noticeably faster than Phase 10 (no chunk
   loop). Each row's summary cell renders.
2. `SELECT title, length(full_text), chunk_count, status FROM documents;` —
   every row has non-null `full_text`, `chunk_count = 1`, `status = complete`.
3. Qdrant `points_count` for the `documents` collection equals the number of
   uploaded docs (one per doc).
4. Ask a content question matching one note in chat. Agent log shows
   `find_documents` then `load_document` firing in order; the final answer
   contains content present in the full text but not in the summary (proof
   the agent loaded the doc, not just summarized).
5. `grep -RIn 'search_documents' agent/ frontend/ docs/` returns nothing
   except the deliberate "removed" mention in CLAUDE.md and historical phase
   docs.
6. Existing services unaffected: `/system` still green across the board,
   `/internships` and `/leetcode` unchanged.

---

## What was built

### Schema
Appended to `scripts/migrate.sql`:

```sql
-- Phase 11: Summary-based RAG — cache extracted full text on the catalog row
-- so the agent's load_document tool can return whole documents without
-- re-parsing the file from the PVC.
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS full_text TEXT;
```

Idempotent. `chunk_count` is now vestigial (1 on `complete`, 0 while
`processing`); kept to avoid a destructive migration. `status` remains the
source of truth.

### Ingestion (`ingestion/main.py`)

Dropped the `SentenceSplitter` import. Rewrote `_embed_and_summarize` as a
six-step summary-routing flow that preserves every Phase 10 reliability
guarantee:

1. Extract full text via `SimpleDirectoryReader` and join with `"\n\n"`.
   Reader exception → `_mark_failed("text extraction failed")`. Empty join →
   `_mark_failed("no extractable text")`.
2. Generate the summary via `_generate_summary(full_text)` (unchanged: first
   2000 chars, gemma4:e2b, `think:false`, `num_ctx:2048`, `num_predict:150`).
   Exception → `_mark_failed("summary generation failed")`. Empty/whitespace
   result → `_mark_failed("summary generation returned empty")`. **This is
   the divergence from Phase 10**, where missing summary was a partial
   success.
3. Embed the summary via `_embed(summary)`. Exception →
   `_mark_failed("embedding call failed")`.
4. Upsert ONE Qdrant point with payload `{document_id, title, summary}`.
   Exception → `_mark_failed("qdrant upsert failed")`.
5. `UPDATE documents SET full_text=%s, summary=%s, chunk_count=1` for the
   row. Exception → `_mark_failed("catalog update failed")`.
6. `_mark_complete(document_id)`, then `_regenerate_toc()` inside its own
   try/except so a TOC failure cannot roll the row back to `failed`.

Outer `try/except Exception` around the whole worker as the Phase 10 safety
net. The reaper job (10 min interval, 30 min threshold) is unchanged.

The pre-existing re-ingest cleanup in `_insert_catalog_row` (delete old
Qdrant points by `document_id` filter) needed no change — under the new
model there's exactly one point per `document_id`, so the same filter-delete
just removes that single point.

### Agent (`agent/main.py`)

Removed the `search_documents` tool entirely. Added two:

- **`find_documents(query: str)`** — embeds the query via Ollama
  `nomic-embed-text`, POSTs `/collections/documents/points/search` with
  `limit=3`, returns each hit as `[score=0.NN] <title> (id=<uuid>)\n<summary>`
  separated by blank lines. Empty result → `"No matching documents found."`
- **`load_document(identifier: str)`** — accepts a UUID OR a
  filename/title substring. UUID path: `WHERE id::text = %s`. Fallback path:
  `WHERE filename ILIKE %s OR title ILIKE %s ORDER BY added_at DESC LIMIT 1`.
  Returns `f"{title}\n\n{full_text}"`. Polite degradation for the unlikely
  case where `full_text IS NULL`.

`list_documents`, `get_table_of_contents`, `get_document_summary` are
unchanged — they browse the catalog rather than search content and remain
useful alongside the routing flow.

`SYSTEM_PROMPT` rewritten to describe the two-step flow explicitly: call
`find_documents` to identify the relevant document(s), then `load_document`
to read the full text, then answer from that full text. The summary returned
by `find_documents` is for finding only — never answer substantive content
questions from it.

### Frontend
No changes. The Documents view already reads `status` directly (Phase 10);
`chunk_count` in the React `Document` interface stays as a vestigial field
to keep the response shape stable. No nginx changes — `/chat` was already
proxied to the agent.

---

## Build process

Standard CLAUDE.md flow — build on xdev-sr (where docker lives), no gzip,
import on the node where the pod runs, kubectl rolls from vlinux1.

```bash
# xdev-sr — build both images
ssh ubuntu@192.168.96.201
cd ~/athena && git pull
sudo docker build -t athena-ingestion:phase11 -f ingestion/Dockerfile ingestion/
sudo docker build -t athena-agent:phase11     -f agent/Dockerfile     agent/
sudo docker save -o /tmp/athena-ingestion.tar athena-ingestion:phase11
sudo docker save -o /tmp/athena-agent.tar     athena-agent:phase11
sudo chmod 644 /tmp/athena-*.tar
sudo k3s ctr images import /tmp/athena-agent.tar   # agent pod is on xdev-sr

# vlinux2 — pull and import ingestion (ingestion pod is on vlinux2)
ssh ubuntu@192.168.96.202
scp ubuntu@192.168.96.201:/tmp/athena-ingestion.tar /tmp/
sudo k3s ctr images import /tmp/athena-ingestion.tar

# vlinux1 — wipe old data, apply migration, then roll
POD=$(kubectl -n athena get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}')

# Wipe (test data only — clean slate)
kubectl -n athena port-forward svc/qdrant 6333:6333 &
sleep 2; curl -X DELETE http://localhost:6333/collections/documents; kill %1
kubectl -n athena exec "$POD" -- psql -U athena -d athena -c "TRUNCATE documents;"
kubectl -n athena exec deploy/ingestion -- sh -c \
  'cd /data/documents && ls -A | grep -v "^_" | xargs -r rm -v'

# Schema migration
kubectl cp scripts/migrate.sql "athena/$POD:/tmp/migrate.sql"
kubectl -n athena exec "$POD" -- psql -U athena -d athena -f /tmp/migrate.sql

# Roll
kubectl -n athena set image deploy/ingestion ingestion=athena-ingestion:phase11
kubectl -n athena set image deploy/agent     agent=athena-agent:phase11
kubectl -n athena rollout status deploy/ingestion
kubectl -n athena rollout status deploy/agent
```

The migration must run **before** the agent rolls — the new agent SELECTs
`full_text` from the documents table and would 500 against the un-migrated
schema.

---

## Issues encountered

None worth recording. Phase 10's heredoc-leftover migrate.sql trap was
sidestepped by following the codified rule (push from Windows through git,
never hand-edit on the node).

---

## Next phase
TBD — to be decided in a Claude.ai planning chat. Candidates carrying over:
Phase 4 (Rust MCP server, now with a cleaner retrieval contract to expose),
email ingestion into the documents pipeline, Twilio SMS notifications, or
revisiting summary-routing if very long documents enter the corpus.
