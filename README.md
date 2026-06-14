# Athena

> A self-hosted AI assistant for internship tracking, LeetCode prep, research, and personal knowledge management.
> Running on a bare-metal k3s cluster with local and cloud LLMs, a LangGraph orchestration layer, and a React dashboard.

## Status
**Phase 11 complete** — Summary-based RAG. One vector per document over its gemma4:e2b summary; the agent's `find_documents` tool routes by summary similarity, `load_document` returns the full text from Postgres. Replaces the per-chunk pipeline from Phase 3.

## Architecture

```
User
  │
  ▼
React Frontend (athena.local)
  │  proxies /chat, /internships, /leetcode
  ▼
LangGraph Agent (agent.local)
  │  mode=chat → GPT-4o-mini (OpenAI)
  │  mode=background → gemma4:e2b (Ollama)
  │
  ├── web_search() ──────────────► SearXNG (searxng.local)
  ├── find_documents(query) ─────► Qdrant (qdrant.local)
  │     └── summary-vector search (limit 3) via nomic-embed-text (Ollama)
  ├── load_document(id|title) ───► PostgreSQL documents.full_text
  └── lookup_leetcode() ──────────► PostgreSQL

Background Services (APScheduler, vlinux2)
  ├── Internship Hunter — scrapes GitHub README tables, scores with Ollama, stores in Postgres
  └── LeetCode Poller  — polls LeetCode GraphQL, syncs submissions to Postgres

Ingestion Pipeline (ingest.local)
  └── POST /ingest → LlamaIndex extract → gemma4:e2b summary → nomic-embed-text on summary
                  → one Qdrant point per document + cache full_text in Postgres
```

## Stack

| Component | Technology | Status |
|-----------|-----------|--------|
| Cluster | k3s + Traefik | ✅ Running |
| Agent orchestration | LangGraph + FastAPI | ✅ Running |
| Chat LLM | OpenAI GPT-4o-mini | ✅ Running |
| Background LLM | Gemma 4 via Ollama (gemma4:e2b) | ✅ Running |
| Embeddings | nomic-embed-text via Ollama | ✅ Running |
| Vector DB | Qdrant v1.13.6 | ✅ Running |
| Relational DB | PostgreSQL 16 | ✅ Running |
| Search | SearXNG | ✅ Running |
| Document ingestion | LlamaIndex | ✅ Running |
| Internship tracking | APScheduler pipeline | ✅ Running |
| LeetCode tracking | APScheduler pipeline | ✅ Running |
| Frontend | React + Vite + Tailwind + nginx | ✅ Running |
| MCP server | Rust (axum, tokio) | 🔲 Not started |
| Notifications | Twilio SMS | 🔲 Planned |
| Automation | n8n | 🔲 Planned |

## Phases

- [x] Phase 1 — Cluster foundation (k3s, Traefik, PostgreSQL, Qdrant, Ollama, SearXNG)
- [x] Phase 2 — LangGraph agent (ReAct loop, tool routing, FastAPI)
- [x] Phase 3 — RAG pipeline (LlamaIndex ingestion, nomic-embed-text, Qdrant search) — superseded by Phase 11
- [x] Phase 5 — Internship hunter (GitHub README scraper, LLM scoring, daily cron)
- [x] Phase 6 — LeetCode poller + frontend dashboard (GraphQL sync, React + recharts)
- [x] Phase 7 — Model router (GPT-4o-mini for chat, gemma4:e2b for background)
- [x] Phase 8 — Multi-chat conversations (Postgres-backed history, conversation sidebar)
- [x] Phase 9 — Document storage & catalog (PVC, folder watcher, summaries, TOC)
- [x] Phase 10 — Ingestion reliability + system health (status column, reaper, /system view)
- [x] Phase 11 — Summary-based RAG (one vector per document, find_documents + load_document)
- [ ] Phase 4 — Rust MCP server
- [ ] Notifications + daily digest (Twilio, n8n)

## Hardware

| Node | IP | RAM | Role |
|------|----|-----|------|
| vlinux1 | 192.168.96.200 | 8GB | k3s control plane, PostgreSQL, Traefik |
| vlinux2 | 192.168.96.202 | 16GB | Frontend, internship hunter, LeetCode poller |
| xdev-sr | 192.168.96.201 | 16GB | Ollama, Qdrant, SearXNG, Agent, Ingestion |

All inference is CPU-only. No GPUs.

## Docs

- [Architecture](docs/architecture.md)
- [Phase Notes](docs/phases/)
- [Architecture Decision Records](docs/adr/)

## Local Hostnames

All `.local` hostnames resolve to `192.168.96.200` (Traefik on the control plane). Add entries to `/etc/hosts`:

```
192.168.96.200 athena.local agent.local ingest.local qdrant.local ollama.local searxng.local
```
