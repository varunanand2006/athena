# Athena — Claude Code Context

## What this project is
Athena is a self-hosted AI assistant running on a bare-metal k3s cluster.
It helps with internship tracking, LeetCode prep, research, and personal knowledge management.
Think JARVIS — a background brain that surfaces relevant information and handles routine tasks.

## Planning vs building
**All architecture decisions and phase planning happen in Claude.ai chat.**
**All implementation work happens here in Claude Code.**

If you're unsure whether something is a planning question or an implementation question, err toward implementing and noting any assumptions made.

## Repo structure
- `cluster/` — Kubernetes manifests (k3s, Traefik, node configs)
- `agent/` — LangGraph orchestration service (Python); interactive chat uses GPT-4o-mini, background tasks use gemma4:e2b
- `mcp-server/` — Rust MCP server (thin proxy, LAN-only; Phase 13 added bearer-token auth)
- `ingestion/` — Document ingestion pipelines (LlamaIndex, Python)
- `internship/` — Internship hunter service (APScheduler, daily pipeline)
- `leetcode/` — LeetCode poller service (APScheduler, daily GraphQL sync + Ollama analysis)
- `frontend/` — React web app (Vite + TypeScript + Tailwind); served by nginx on vlinux2; proxies `/chat /conversations /internships /leetcode /healthz /documents /system /memory` to the agent and `/ingest /toc` to the ingestion service
- `scripts/` — Setup and utility scripts (k3s setup, DB migrations, OAuth token minting)
- `docs/` — Architecture docs (`docs/architecture.md`), phase write-ups (`docs/phases/`), ADRs (`docs/adr/`)
- `/data/documents` (PVC on vlinux2) — source-of-truth file store for the document library
- `/data/memory` (PVC on xdev-sr) — agent memory vault (Obsidian-native markdown notes)

## Hardware
- `vlinux1` — 192.168.96.200, 8GB RAM, k3s control plane
- `vlinux2` — 192.168.96.202, 16GB RAM, `workload=services`; runs internship hunter, leetcode poller, ingestion, frontend (`athena.local`)
- `xdev-sr` — 192.168.96.201, 16GB RAM, `workload=ai`; docker installed here — use for image builds; agent/ollama/qdrant run here
- `varunlaptop` — 192.168.96.13, personal laptop (not a cluster node; used for SSH/kubectl only)

## Tech stack
- **k3s** with Traefik ingress, Flannel networking
- **Ollama** running Gemma 4 (local inference, CPU only — expect slow responses)
  - Current model: `gemma4:e2b` (5.12B params, Q4_K_M, 7.2GB). Swap to `gemma4:12b` for real workloads.
- **LangGraph** for agent orchestration
- **LlamaIndex** for document parsing and ingestion
- **Qdrant** for vector search (summary-level, one vector per document)
- **PostgreSQL** for relational data
- **Rust** for the MCP server (`axum`, `tokio`, `thiserror`)
- **React / Vite / Tailwind** for the frontend
- **n8n** for scheduled pipelines (planned)
- **Twilio** for SMS notifications (planned)

## Current phase
**Phase 20 (complete)** — Gmail + Google Calendar read-only lookup.

Both are on-demand lookup sources (like `load_document` / `lookup_leetcode`) — the agent calls them when asked, they do NOT auto-feed memory.

- `search_email(query)` → `agent/gmail_client.py` → `gmail-secret` k8s secret
- `get_calendar_events(timeframe)` → `agent/calendar_client.py` → `gcal-secret` k8s secret
- OAuth scopes: `gmail.readonly` + `calendar.readonly` **only** — credentials are physically incapable of writes
- Both secrets are `optional: true` so the agent starts before they exist
- Neither is exposed via the Rust MCP server (kept off the tunnel-facing surface)

See `docs/phases/phase-19-gmail-readonly.md`, `docs/adr/011-gmail-readonly-lookup.md`, and `docs/phases/` for all prior phase write-ups.

### Phase history (brief)
| Phase | Summary |
|-------|---------|
| 1–11  | Cluster bootstrap → agent → RAG → internships → LeetCode → frontend → multi-chat → document storage → health → summary-RAG |
| 12    | Rust MCP server (LAN-only thin proxy) |
| 13    | MCP bearer-token auth + Cloudflare Tunnel |
| 14    | Agent memory vault (Obsidian markdown on PVC) |
| 15    | Automatic memory capture (watermark-triggered reflection) |
| 16    | Ambient memory recall (full-vault load into system prompt) |
| 17    | Temporal frontmatter (`events:` list on dated notes) |
| 18    | Interlinked memory / wiki graph (`[[wikilinks]]`, concept pages) |
| 19–20 | Gmail + Google Calendar read-only lookup |

## Coding conventions
- Python services use `pyproject.toml`, not `requirements.txt`
- Kubernetes manifests are raw YAML (no Helm unless the upstream repo requires it)
- Rust code should be idiomatic — use `thiserror`, `tokio`, `axum`
- Commit format: `type(scope): description` — e.g. `feat(agent): add web search tool`
- Never commit secrets, `.env` files, or kubeconfig

## What not to do
- Don't suggest cloud-hosted alternatives to self-hosted components
- Don't add Helm charts unless asked
- Don't implement the next phase unless explicitly told to move forward
- Don't restructure the repo layout without asking first

@docs/claude/key-lessons.md
