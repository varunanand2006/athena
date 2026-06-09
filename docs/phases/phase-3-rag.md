# Phase 3 — RAG Pipeline & PostgreSQL Schema

## Goal
Two parallel tracks: a LlamaIndex ingestion service that stores personal documents in Qdrant, and a PostgreSQL schema for structured data. The agent gains a `search_documents` tool so it can ground responses in the user's own content.

## Phase gate
Upload a PDF resume to `ingest.local/ingest`, then POST to `agent.local/chat` asking about work experience — the response references specific content from the resume, not generic advice.

---

## What was built

### Ingestion service (`ingestion/`)
- FastAPI app with `POST /ingest` accepting multipart file uploads
- LlamaIndex `SimpleDirectoryReader` for PDF/text parsing
- `SentenceSplitter` chunking: 512-token chunks, 64-token overlap
- Embeds each chunk via direct `POST /api/embeddings` to Ollama (nomic-embed-text)
- Upserts points directly to Qdrant `documents` collection via `qdrant-client`
- Payload per point: `{"text": "...", "filename": "..."}`
- Scheduled on xdev-sr (workload=ai), Traefik ingress at `ingest.local`

### Agent tool: `search_documents`
- Added to `agent/main.py` alongside the existing `web_search` tool
- Embeds the query via `POST /api/embeddings` to Ollama (same model: nomic-embed-text)
- Searches Qdrant `documents` collection via REST (`POST /collections/documents/points/search`)
- Returns top-5 hits with score and truncated text (400 chars per chunk)
- System prompt updated to prefer this tool for personal/resume/skills questions

### PostgreSQL schema (`scripts/migrate.sql`)
Three tables, no agent integration yet — schema in place for future phases:
- `applications` (id, company, role, stage, applied_date, notes, created_at, updated_at)
- `tasks` (id, title, deadline, source, status, created_at, updated_at)
- `contacts` (id, name, company, email, notes, created_at, updated_at)

Applied with: `kubectl exec -i -n athena <postgres-pod> -- psql -U athena -d athena < scripts/migrate.sql`

---

## Issues encountered

### LlamaIndex VectorStoreIndex silently drops Qdrant writes
Using `VectorStoreIndex(nodes, vector_store=vector_store)` and even `VectorStoreIndex(nodes, storage_context=StorageContext.from_defaults(vector_store=...))` both produced 0 points in Qdrant — no errors, no warnings. Root cause: LlamaIndex's internal embedding/storage pipeline didn't fire as expected with the Ollama embed integration.

Fix: bypass VectorStoreIndex entirely. Use LlamaIndex only for parsing and chunking, then embed and upsert manually via httpx + qdrant-client. This is more explicit and reliable.

### Ingestion image running stale code
Changes to `ingestion/main.py` on the laptop weren't committed and pushed before rebuilding on xdev-sr. The `docker build` used the old file from `git pull`, which hadn't changed. Fix: always commit and push from the laptop before building on xdev-sr.

### `kubectl exec` stdin requires `-i`
Piping SQL into `kubectl exec -- psql` silently runs nothing without the `-i` flag. Correct form:
```bash
kubectl exec -i -n athena <pod> -- psql -U athena -d athena < scripts/migrate.sql
```

---

## Build process
Same as Phase 2 — no registry, build on xdev-sr and import directly:
```bash
sudo docker build -t athena-ingestion:latest ingestion/
sudo docker save athena-ingestion:latest | sudo k3s ctr images import -
```

## /etc/hosts entries required
```
192.168.96.200  ingest.local
```

---

## Next phase
Phase 4 — React frontend and n8n pipelines for automated ingestion (email monitoring, internship tracker writes).
