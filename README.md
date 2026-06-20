# Athena

> A self-hosted AI assistant for internship tracking, LeetCode prep, research, and personal knowledge management.
> Running on a bare-metal k3s cluster with local and cloud LLMs, a LangGraph orchestration layer, a persistent memory vault, and a React dashboard.

## Status
**Phase 21 complete** — Safe in-chat memory correction + automatic external source feeds. The agent has a persistent, interlinked memory vault (Obsidian markdown) that it both reads (ambient recall every turn) and writes (background reflection, explicit capture, and `calendar`/labeled-`email` feeds), plus read-only Gmail and Google Calendar lookups. A Rust MCP server exposes the document/LeetCode tools to Claude Code over an authenticated tunnel.

## Architecture

```
User
  │
  ▼
React Frontend (athena.local)
  │  proxies /chat /chat/stream /conversations /internships /leetcode
  │         /documents /system /memory  → agent
  │         /ingest /toc                → ingestion
  ▼
LangGraph Agent (agent.local)
  │  mode=chat → GPT-4o-mini (OpenAI), streamed over SSE (/chat/stream)
  │  mode=background → gemma4:e2b (Ollama)
  │  ambient recall: the whole memory vault is loaded into the system prompt each turn
  │
  ├── web_search() ───────────────► SearXNG (searxng.local)
  ├── find_documents(query) ──────► Qdrant (qdrant.local)
  │     └── summary-vector search (limit 3) via nomic-embed-text (Ollama)
  ├── load_document(id|title) ────► PostgreSQL documents.full_text
  ├── lookup_leetcode() ───────────► PostgreSQL
  ├── write_memory / update_memory / search_memory / list_memories / upcoming
  │     └──────────────────────────► /data/memory vault (markdown notes on xdev-sr PVC)
  ├── search_email(query) ─────────► Gmail API (read-only)
  └── get_calendar_events(tf) ─────► Google Calendar API (read-only)

Background memory (threads + APScheduler, in-agent)
  ├── reflect_on_conversation() — new-conversation boundary + 30-min straggler sweep
  ├── reflect_on_calendar()     — sweeps upcoming events into notes (origin=calendar)
  └── reflect_on_labeled_email()— ingests ONLY label:athena email (origin=email)

Background Services (APScheduler, vlinux2)
  ├── Internship Hunter — scrapes GitHub README tables, scores with Ollama, stores in Postgres
  └── LeetCode Poller  — polls LeetCode GraphQL, syncs submissions, analyzes with Ollama

Ingestion Pipeline (ingest.local)
  └── POST /ingest → LlamaIndex extract → gemma4:e2b summary → nomic-embed-text on summary
                  → one Qdrant point per document + cache full_text in Postgres

MCP Server (mcp.local, Rust)
  └── streamable-HTTP proxy → agent /tools/* ; bearer-token auth + Cloudflare Tunnel
      exposes find_documents, load_document, lookup_leetcode to Claude Code
```

## Stack

| Component | Technology | Status |
|-----------|-----------|--------|
| Cluster | k3s + Traefik | ✅ Running |
| Agent orchestration | LangGraph + FastAPI | ✅ Running |
| Chat LLM | OpenAI GPT-4o-mini (SSE streaming) | ✅ Running |
| Background LLM | Gemma 4 via Ollama (gemma4:e2b) | ✅ Running |
| Embeddings | nomic-embed-text via Ollama | ✅ Running |
| Vector DB | Qdrant v1.13.6 | ✅ Running |
| Relational DB | PostgreSQL 16 | ✅ Running |
| Search | SearXNG | ✅ Running |
| Document ingestion | LlamaIndex | ✅ Running |
| Memory vault | Obsidian-native markdown on PVC (`/data/memory`) | ✅ Running |
| Gmail / Calendar | Google APIs, read-only (`*.readonly` scopes) | ✅ Running |
| Internship tracking | APScheduler pipeline | ✅ Running |
| LeetCode tracking | APScheduler pipeline | ✅ Running |
| Frontend | React + Vite + Tailwind + nginx | ✅ Running |
| MCP server | Rust (axum, tokio), bearer auth + Cloudflare Tunnel | ✅ Running |
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
- [x] Phase 12 — Rust MCP server (LAN-only thin proxy over `/tools/*`)
- [x] Phase 13 — MCP bearer-token auth + Cloudflare Tunnel
- [x] Phase 14 — Agent memory vault (Obsidian markdown on PVC)
- [x] Phase 15 — Automatic memory capture (watermark-triggered reflection)
- [x] Phase 16 — Ambient memory recall (full-vault load into the system prompt)
- [x] Phase 17 — Temporal frontmatter (`events:` on dated notes, `upcoming` tool)
- [x] Phase 18 — Interlinked memory / wiki graph (`[[wikilinks]]`, concept pages)
- [x] Phase 19 — Gmail read-only lookup (`search_email`)
- [x] Phase 20 — Google Calendar read-only lookup (`get_calendar_events`)
- [x] Phase 21 — Safe memory correction (`update_memory`) + automatic calendar/labeled-email feeds
- [ ] Notifications + daily digest (Twilio, n8n)

## Hardware

| Node | IP | RAM | Role |
|------|----|-----|------|
| vlinux1 | 192.168.96.200 | 8GB | k3s control plane, PostgreSQL, Traefik |
| vlinux2 | 192.168.96.202 | 16GB | Frontend, internship hunter, LeetCode poller, ingestion |
| xdev-sr | 192.168.96.201 | 16GB | Ollama, Qdrant, SearXNG, Agent, MCP server, memory vault PVC |

All inference is CPU-only. No GPUs.

## Docs

- [Architecture](docs/architecture.md)
- [Phase Notes](docs/phases/)
- [Architecture Decision Records](docs/adr/)
- [MCP Server](mcp-server/README.md)
- [Claude Code context](CLAUDE.md) · [Key implementation lessons](docs/claude/key-lessons.md)

## Local Hostnames

All `.local` hostnames resolve to `192.168.96.200` (Traefik on the control plane). Add entries to `/etc/hosts`:

```
192.168.96.200 athena.local agent.local ingest.local qdrant.local ollama.local searxng.local mcp.local
```
