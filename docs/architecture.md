# Athena — System Architecture

## What it is
Athena is a self-hosted AI assistant running on a bare-metal k3s cluster. It acts as a background brain — tracking internship applications, surfacing LeetCode progress, answering questions with document-backed context, and handling routine tasks via natural language. Think JARVIS: proactive, persistent, and entirely self-hosted.

---

## Hardware

| Node | IP | RAM | Role |
|------|----|-----|------|
| vlinux1 | 192.168.96.200 | 8GB | k3s control plane, PostgreSQL, Traefik ingress |
| vlinux2 | 192.168.96.202 | 16GB | Frontend, internship hunter, LeetCode poller |
| xdev-sr | 192.168.96.201 | 16GB | Ollama, Qdrant, SearXNG, Agent, Ingestion |
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
| Ingestion | LlamaIndex + FastAPI | ✅ Running | Document upload, chunking, embedding pipeline |
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
      │  proxies /chat, /internships, /leetcode
      ▼
LangGraph Agent — agent.local (xdev-sr)
      │
      ├─ mode=chat ──────► OpenAI GPT-4o-mini (cloud)
      ├─ mode=background ► Ollama gemma4:e2b (xdev-sr)
      │
      ├─ web_search() ───► SearXNG — searxng.local (xdev-sr)
      ├─ search_docs() ──► Qdrant — qdrant.local (xdev-sr)
      │                        ▲
      │                        │ embed via nomic-embed-text
      │                        │
      │              POST /ingest → LlamaIndex → Qdrant
      │              (ingest.local, xdev-sr)
      │
      └─ lookup_leetcode() ─► PostgreSQL (vlinux1)

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
| `ingest.local` | LlamaIndex ingestion | xdev-sr |
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
| Ingestion | xdev-sr | `workload=ai` | Direct access to Ollama and Qdrant |
| Frontend | vlinux2 | `kubernetes.io/hostname: vlinux2` | Lightweight, offloads xdev-sr |
| Internship hunter | vlinux2 | `kubernetes.io/hostname: vlinux2` | Lightweight poller, calls agent and SearXNG remotely |
| LeetCode poller | vlinux2 | `kubernetes.io/hostname: vlinux2` | Lightweight poller |

---

## Database Schema

### PostgreSQL tables

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
