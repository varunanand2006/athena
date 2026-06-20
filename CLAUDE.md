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
**Phase 21 (complete)** — Safe in-chat memory correction + automatic external source feeds.

Two extensions to *how memory gets written* (vault format unchanged except a new `origin` field):

- **Foreground correction** — `update_memory(title, content, tags, events)` tool (`agent/main.py`) REPLACES an existing note's body and dated events instead of appending. Fires ONLY on explicit correction language ("update", "change", "correct", "actually it's", "moved to", "reschedule", "no longer") — prompt-enforced, **re-verify on any foreground-model swap** (same discipline as the explicit-only `write_memory` rule). `write_memory` also now accepts `events`; the chat prompt carries today's date for relative-date resolution. Backed by `memory.write_note(replace=True, replace_events=True)`. **Foreground-only** — background reflection NEVER gets `replace_events`.
- **Calendar feed** — `reflection.reflect_on_calendar()` sweeps the next 14 days of Google Calendar (fully automatic — calendar is curated by definition) and writes durable events as notes with `source: auto`, `origin: calendar`. Throttled by a `_calendar_sweep.md` vault watermark.
- **Email feed** — `reflection.reflect_on_labeled_email()` ingests ONLY emails carrying the Gmail label `athena` (env `ATHENA_EMAIL_LABEL`); writes `source: auto`, `origin: email`. **The full inbox is NEVER swept** — the label is mandatory. Processed message IDs tracked in the Postgres `email_processed` table.
- Both sweeps run at the new-conversation boundary AND the 30-min straggler job (`_run_external_feeds()`), are **append-only** (no destructive background rewrites), and **degrade silently** if `gcal-secret`/`gmail-secret` aren't mounted.
- **`origin` frontmatter** — `conversation` (default, backward-compatible) | `calendar` | `email`; preserved across updates like `source`; surfaced as a "from …" chip in `/memory`.

See `docs/phases/phase-21-memory-feeds.md`, `docs/adr/012-external-memory-feeds.md`, and `docs/phases/` for all prior phase write-ups.

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
| 21    | Safe foreground memory correction (`update_memory`) + automatic calendar/labeled-email feeds (`origin` provenance) |

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
