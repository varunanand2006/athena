# Athena — System Architecture

## What it is
Athena is a self-hosted AI assistant running on a bare-metal k3s cluster. It acts as a background brain — tracking internship applications, surfacing LeetCode progress, answering questions with document-backed context, and handling routine tasks via natural language. Think JARVIS: proactive, persistent, and entirely self-hosted.

---

## Hardware

| Node | IP | RAM | Role |
|------|----|-----|------|
| vlinux1 | 192.168.96.200 | 8GB | k3s control plane, PostgreSQL, Traefik ingress |
| vlinux2 | 192.168.96.202 | 16GB | Frontend, internship hunter, LeetCode poller, Ingestion (+ document PVC) |
| xdev-sr | 192.168.96.201 | 16GB | Ollama, Qdrant, SearXNG, Agent |
| varunlaptop | 192.168.96.13 | — | Personal laptop, not a cluster node |

All inference is CPU-only. No GPUs.

---

## Components

| Component | Technology | Status | Purpose |
|-----------|-----------|--------|---------|
| Cluster | k3s + Traefik | ✅ Running | Kubernetes orchestration, ingress routing |
| Agent | LangGraph + FastAPI | ✅ Running | Multi-step reasoning, tool routing, chat API |
| Inference (chat) | OpenAI GPT-4o-mini | ✅ Running | Fast interactive responses |
| Inference (background) | Ollama gemma4:e2b | ✅ Running | Local LLM for pipeline tasks |
| Embeddings | Ollama nomic-embed-text | ✅ Running | Text → vector for semantic search |
| Vector store | Qdrant v1.13.6 | ✅ Running | Semantic search over ingested documents |
| Relational DB | PostgreSQL 16 | ✅ Running | Internship postings, LeetCode data |
| Search | SearXNG | ✅ Running | Web search tool for the agent |
| Ingestion | LlamaIndex + FastAPI + APScheduler | ✅ Running | Document upload, persistent PVC store, full-text caching, summarization, one summary vector per document, folder watcher, catalog (Postgres) + TOC |
| Document PVC | k3s local-path (10Gi, vlinux2) | ✅ Running | Source-of-truth file store at `/data/documents`; survives pod restarts |
| Internship hunter | APScheduler (Python) | ✅ Running | Daily GitHub README scrape → LLM score → Postgres |
| LeetCode poller | APScheduler (Python) | ✅ Running | Daily GraphQL sync, Ollama analysis queue |
| Frontend | React + Vite + nginx | ✅ Running | Chat UI, internship dashboard, LeetCode stats |
| MCP server | Rust (axum, tokio) | 🔲 Not started | Custom tool definitions for the agent |
| Automation | n8n | 🔲 Planned | Scheduled pipelines, email polling |
| Notifications | Twilio | 🔲 Planned | SMS alerts for high-priority events |

---

## Data Flow

```
User (browser)
      │
      ▼
React Frontend — athena.local (nginx, vlinux2)
      │  proxies /chat /conversations /internships /leetcode /healthz /documents → agent
      │  proxies /ingest /toc                                                    → ingestion
      ▼
LangGraph Agent — agent.local (xdev-sr)
      │
      ├─ mode=chat ──────► OpenAI GPT-4o-mini (cloud)
      ├─ mode=background ► Ollama gemma4:e2b (xdev-sr)
      │
      ├─ web_search() ───────────────► SearXNG — searxng.local (xdev-sr)
      ├─ find_documents(query) ──────► Qdrant — qdrant.local (xdev-sr)
      │                                    summary-vector search (limit 3)
      ├─ load_document(id_or_title) ─► PostgreSQL `documents.full_text` (vlinux1)
      ├─ list_documents() ───────────► PostgreSQL `documents` (vlinux1)
      ├─ get_table_of_contents() ────► Ingestion GET /toc (vlinux2)
      ├─ get_document_summary(name) ─► PostgreSQL `documents` (vlinux1)
      │
      ├─ lookup_leetcode() ──────────► PostgreSQL (vlinux1)
      └─ conversation history ───────► PostgreSQL conversations/messages (vlinux1)

GET /conversations, GET /conversations/:id/messages, DELETE /conversations/:id
GET /documents (catalog JSON)
      │
      ▼
React frontend — sidebar (conversations), /dashboard, /documents

Document storage data flow
==========================

Frontend upload (POST /ingest)            Folder drop (scp file → /data/documents)
        │                                                │
        ▼                                                │
Ingestion — ingest.local (vlinux2)                       │
        │                                                │
        │   write file → /data/documents/<name>          │   BackgroundScheduler
        │                                                │   scans every 5 min
        │   _insert_catalog_row()                        │   _insert_catalog_row()
        │     re-ingest? qdrant filter-delete            │   _embed_and_summarize()
        │     INSERT documents row → document_id         │     (sync — in scheduler thread)
        │                                                │
        │   threading.Thread(_embed_and_summarize) ──────┤
        │                                                │
        │   return IngestResponse (fast)                 ▼
        ▼                                          Same _embed_and_summarize path
   Frontend polls GET /documents every 4s              │
   until status != 'processing' (then summary renders)  ▼

_embed_and_summarize (summary-routing, Phase 11):
  SimpleDirectoryReader → join all extracted pieces into one full_text
  → first 2000 chars → Ollama gemma4:e2b /api/chat (think:false, num_ctx 2048, num_predict 150)
    (summary is REQUIRED — empty summary marks the row failed)
  → Ollama nomic-embed-text on the summary → one 768-dim vector
  → Qdrant upsert ONE point with payload {document_id, title, summary}
    (one point per document; document_id stamped for filter-delete on re-ingest/delete)
  → UPDATE documents SET full_text, summary, chunk_count=1
  → _mark_complete (status → 'complete')
  → _regenerate_toc() → write atomic /data/documents/_TABLE_OF_CONTENTS.md

Document delete (DELETE /ingest/documents/{id})
  → qdrant filter-delete by document_id
  → DELETE FROM documents
  → Path(file_path).unlink()  (must — watcher would re-ingest otherwise)
  → _regenerate_toc()

Background Services (vlinux2, APScheduler)
      ├─ Internship Hunter (06:00 ET daily)
      │     GitHub README → parse → dedupe → SearXNG research
      │     → Ollama score → PostgreSQL internship_postings
      │
      └─ LeetCode Poller (daily)
            LeetCode GraphQL → PostgreSQL leetcode_problems/submissions
            → Ollama analysis queue
```

---

## Networking

- All services run in the `athena` namespace
- Inter-service communication via ClusterIP (e.g. `postgres.athena.svc.cluster.local:5432`)
- External access via Traefik ingress (HTTP, entrypoint=web):

| Hostname | Service | Node |
|----------|---------|------|
| `athena.local` | React frontend | vlinux2 |
| `agent.local` | LangGraph agent | xdev-sr |
| `ingest.local` | LlamaIndex ingestion | vlinux2 |
| `qdrant.local` | Qdrant HTTP API | xdev-sr |
| `ollama.local` | Ollama API | xdev-sr |
| `searxng.local` | SearXNG search API | xdev-sr |

All `.local` hostnames resolve to `192.168.96.200` (Traefik on the control plane).

---

## Node Scheduling

| Workload | Node | Selector | Reason |
|----------|------|----------|--------|
| PostgreSQL | vlinux1 | (control plane default) | Stable, low memory use |
| Ollama | xdev-sr | `workload=ai` | 16GB RAM needed for model weights |
| Qdrant | xdev-sr | `workload=ai` | Vector ops benefit from dedicated resources |
| SearXNG | xdev-sr | `workload=ai` | Co-located with agent |
| Agent | xdev-sr | `workload=ai` | Direct access to Ollama and Qdrant |
| Ingestion | vlinux2 | `kubernetes.io/hostname: vlinux2` | Pinned to the node that holds the documents PVC (k3s local-path is node-local) |
| Frontend | vlinux2 | `kubernetes.io/hostname: vlinux2` | Lightweight, offloads xdev-sr |
| Internship hunter | vlinux2 | `kubernetes.io/hostname: vlinux2` | Lightweight poller, calls agent and SearXNG remotely |
| LeetCode poller | vlinux2 | `kubernetes.io/hostname: vlinux2` | Lightweight poller |

---

## Database Schema

### PostgreSQL tables

**conversations** — `id UUID PK, title TEXT, created_at, updated_at`

**messages** — `id UUID PK, conversation_id UUID FK→conversations, role TEXT, content TEXT, created_at` — index on `conversation_id`

**internship_postings**
```sql
company TEXT, role TEXT, location TEXT, priority_score INT,
resume_recommendation TEXT, company_summary TEXT,
apply_link TEXT, found_date DATE
```

**leetcode_problems** — `slug PK, title, difficulty, solved_at`

**leetcode_submissions** — `id PK, problem_slug, difficulty, submitted_at`

**leetcode_analysis** — `problem_slug, analysis_text, analyzed_at`

**leetcode_queue** — `problem_slug PK, submitted_at, queued_at`

**documents** — catalog rows for ingested files. Each row corresponds to one file on the PVC; `id` is stamped into every related Qdrant point's payload as `document_id`. Under summary-routing RAG (Phase 11) the relationship to Qdrant is 1:1 — one row, one summary vector.
```sql
id          UUID PK DEFAULT gen_random_uuid(),
filename    TEXT NOT NULL UNIQUE,   -- original upload filename
title       TEXT NOT NULL,          -- filename stem
doc_type    TEXT NOT NULL,          -- pdf, txt, md, docx, ...
file_path   TEXT NOT NULL,          -- absolute path on /data/documents PVC
summary     TEXT,                   -- gemma4:e2b one-paragraph summary (retrieval key, REQUIRED for status='complete')
full_text   TEXT,                   -- entire extracted document text, served by load_document tool
chunk_count INTEGER NOT NULL DEFAULT 0,  -- vestigial post-Phase-11: 1 once complete, 0 while processing
size_bytes  INTEGER NOT NULL DEFAULT 0,
status      TEXT NOT NULL DEFAULT 'processing',  -- 'processing' | 'complete' | 'failed' (Phase 10)
added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
```
Index on `(added_at DESC)`.

### Qdrant collections

**documents** — vector store for document summaries (Phase 11). Each point carries one nomic-embed-text embedding of the document's gemma4:e2b summary, with payload `{document_id, title, summary}`. Exactly one point per catalog row. The agent's `find_documents` tool searches these summary vectors for routing; `load_document` then reads `documents.full_text` from Postgres to answer. Distinct from the Postgres table of the same name — they store different things (summary vectors with brief metadata vs. catalog rows with full text). On re-ingest or row deletion, the single point is removed by filter-match on `document_id`.
