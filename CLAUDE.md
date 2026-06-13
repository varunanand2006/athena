# Athena — Claude Code Context

## What this project is
Athena is a self-hosted AI assistant running on a bare-metal k3s cluster.
It helps with internship tracking, LeetCode prep, research, and personal knowledge management.
Think JARVIS — a background brain that surfaces relevant information and handles routine tasks.
Email monitoring and SMS notifications are planned but not yet implemented.

## Planning vs building
**All architecture decisions and phase planning happen in Claude.ai chat.**
**All implementation work happens here in Claude Code (or Google Antigravity).**

If you're unsure whether something is a planning question or an implementation
question, err toward implementing and noting any assumptions made.

## Repo structure
- `cluster/` — Kubernetes manifests (k3s, Traefik, node configs)
- `agent/` — LangGraph orchestration service (Python); interactive chat uses GPT-4o-mini, background tasks use gemma4:e2b
- `mcp-server/` — Custom MCP server (Rust) — not yet implemented
- `ingestion/` — Document ingestion pipelines (LlamaIndex, Python)
- `internship/` — Internship hunter service (APScheduler, daily pipeline)
- `leetcode/` — LeetCode poller service (APScheduler, daily GraphQL sync + Ollama analysis)
- `frontend/` — React web app (Vite + TypeScript + Tailwind); served by nginx on vlinux2, proxies `/chat /conversations /internships /leetcode /healthz /documents /system` to the agent and `/ingest /toc` to the ingestion service
- `scripts/` — Setup and utility scripts (k3s setup, DB migrations, model pulls)
- `docs/` — Architecture docs, phase notes, ADRs
- `/data/documents` (PVC on vlinux2) — source-of-truth file store for the document library; original files persist here, mounted into the ingestion pod

## Hardware
- `vlinux1`  — 192.168.96.200, 8GB RAM, k3s control plane
- `vlinux2`  — 192.168.96.202, 16GB RAM, workload=services; runs internship hunter, leetcode poller, ingestion pipeline, frontend (athena.local)
- `xdev-sr`  — 192.168.96.201, 16GB RAM, workload=ai; docker is installed here — use for image builds
- `varunlaptop` — 192.168.96.13, personal laptop (not a cluster node, used for SSH/kubectl only)

## Tech stack
- **k3s** with Traefik ingress, Flannel networking
- **Ollama** running Gemma 4 (local inference, CPU only — expect slow responses)
  - Phase 1 testing: `gemma4:e2b` (5.12B params, Q4_K_M, 7.2GB)
  - Swap to `gemma4:12b` when moving to real workloads
- **LangGraph** for agent orchestration
- **LlamaIndex** for document parsing and ingestion
- **Qdrant** for vector search
- **PostgreSQL** for relational data
- **Rust** for the MCP server
- **React** for the frontend
- **n8n** for scheduled pipelines
- **Twilio** for SMS notifications

## Current phase
Phase 10 — Ingestion Reliability + System Health. The ingestion service
now tracks a real `status` column on `documents` (`processing` |
`complete` | `failed`) instead of the frontend inferring state from
`chunk_count == 0`. The background thread in `_embed_and_summarize`
calls `_mark_complete(document_id)` after the chunks-and-summary
UPDATE lands, and `_mark_failed(document_id, reason)` at every known
early-return site (text extraction failed, no content, embedding
failure, qdrant upsert failure, catalog UPDATE failure). An outer
`try/except Exception` in the same function is the safety net for
crashes the inner blocks don't cover (e.g. `SentenceSplitter`) —
without it a crashed daemon thread leaves the row stuck at
`processing` forever.

A second APScheduler job in the ingestion `lifespan` runs every
10 min: any row in `status='processing'` for more than 30 min is
flipped to `failed` with a logged reason. This catches rows orphaned
by a pod restart mid-ingest, where the daemon thread that would
have caught the exception no longer exists.

Frontend Documents view reads the new `status` field directly — spinner
while `processing`, red "Failed" badge with "Delete and re-upload to
retry" hint when `failed`, summary text when `complete`. Polling stops
as soon as every row has settled (no more 4 s refetch forever on stuck
rows). Retry = the existing per-row delete button (no auto re-upload).

New agent endpoint `GET /system/health` fans out 2s parallel pings to
ingestion, ollama, qdrant, and searxng via `httpx.AsyncClient` +
`asyncio.gather`, plus a single Postgres round-trip for the data
snapshot (documents total + `by_status`, internships total +
`last_found_date`, leetcode total_solved + `last_solved_at`). Agent
self-check is hardcoded `reachable=true, latency_ms=0` since we're
inside the process. New frontend route `/system` renders five service
rows with green/red status dots + latency and three data cards
(Documents w/ status breakdown, Internships found, LeetCode solved),
auto-refreshing every 15 s.

**Phase 9 context still applies:** The Qdrant collection `documents`
(vector chunks) and the Postgres table `documents` (catalog rows) are
different stores. Catalog row = one document; Qdrant point = one
chunk of that document's text, stamped with the row's `document_id`
in its payload for clean delete-by-filter on re-ingest or row delete.
Original files live on a 10Gi PVC at `/data/documents` on vlinux2, and
a `BackgroundScheduler` watches that folder every 5 min for files
dropped in directly.

## Coding conventions
- Python services use `pyproject.toml`, not `requirements.txt`
- Kubernetes manifests are raw YAML (no Helm unless repo is unavailable)
- Rust code should be idiomatic — use `thiserror`, `tokio`, `axum`
- Commit format: `type(scope): description` — e.g. `feat(agent): add web search tool`
- Never commit secrets, `.env` files, or kubeconfig

## Key lessons
- **APScheduler pattern** — use `BlockingScheduler` from `apscheduler.schedulers.blocking` for polling services; run the pipeline once on startup before handing off to the scheduler so the first deploy is immediately testable
- **gemma4:e2b is a thinking model** — raw `/api/generate` returns empty `response` because all tokens are consumed by internal reasoning. Use `/api/chat` with `"think": false` for structured output tasks. Read from `message.content`, not `response`.
- **Ollama token limits for CPU inference** — always pass `num_ctx: 2048, num_predict: 150` to keep responses fast on CPU; set httpx timeouts to 90s per call
- **Image build workflow** — docker is on xdev-sr; build there, `sudo docker build`, `sudo docker save -o /tmp/image.tar`, `sudo chmod 644`; import must happen on the node where the pod runs (check `kubectl get pods -o wide`); agent/ollama/qdrant/searxng run on xdev-sr so import locally there; frontend/internship/leetcode/ingestion run on vlinux2 so pull via reverse SCP (`scp ubuntu@192.168.96.201:/tmp/image.tar /tmp/`) then import; `kubectl rollout restart` always from vlinux1 or laptop (vlinux2 has no kubeconfig); /tmp is wiped on reboot so rebuild if the tar is gone; **skip gzip** — `docker save | gzip` hangs on CPU-constrained xdev-sr, just `docker save -o file.tar` instead
- **kubectl exec stdin** — piping SQL via `< file` through `kubectl exec` is unreliable; prefer `kubectl cp` then `psql -f`; if the SQL file is only on the dev machine (not on the cluster node), pass it inline with `psql -c "..."` to avoid the copy step entirely
- **k3s cluster survives hard power cuts** — all three nodes (vlinux1, vlinux2, xdev-sr) came back cleanly after a full power outage; k3s and all pods auto-restarted with exactly 1 restart each; Postgres data survived intact on its PV; no manual recovery needed; the only follow-up was running a pending schema migration that hadn't been applied yet before the outage
- **React/Vite multi-stage build** — Node 20 builder stage runs `npm ci && npm run build`; nginx:alpine serves `dist/`; keep `nginx.conf` next to the Dockerfile so the COPY path is predictable
- **Nginx CORS proxy for SPA** — proxy `/chat`, `/internships`, `/leetcode` to the agent ClusterIP so the browser never hits a different origin; set `proxy_read_timeout 120s` for slow LLM responses
- **Pinning pods to vlinux2** — use `nodeSelector: kubernetes.io/hostname: vlinux2`, not a workload label; the existing `workload=inference` label on vlinux2 is unused by any manifest; ingress host `athena.local` points to 192.168.96.200 (control plane, where Traefik runs)
- **Mode-based model routing** — POST /chat accepts an optional `mode` field ("chat" → GPT-4o-mini, "background" → Gemma via Ollama); agent and LangGraph graph are constructed per-request so the LLM swap is seamless
- **langchain-openai** — add to both `pyproject.toml` AND `agent/Dockerfile` pip install list; the Dockerfile doesn't use pyproject.toml so they must be kept in sync manually
- **k8s secrets** — always create secrets with `-n athena`; a secret in the default namespace is invisible to pods in the athena namespace and the pod will start with an empty env var rather than failing loudly
- **Multi-chat history** — pass full conversation history as the `messages` array to `create_react_agent`; load it from Postgres ordered by `created_at ASC` before every /chat call; the agent sees all prior turns as context
- **UUID primary keys** — use `gen_random_uuid()` as the default for UUID PKs in Postgres; returns a `uuid` type, cast to `str` in Python before returning in JSON
- **Postgres schema — conversations/messages** — `conversations(id uuid pk, title text, created_at, updated_at)`; `messages(id uuid pk, conversation_id uuid fk→conversations, role text, content text, created_at)`; index on `messages(conversation_id)`
- **Postgres schema — documents** — `documents(id uuid pk, filename text unique, title text, doc_type text, file_path text, summary text, chunk_count int, size_bytes int, status text default 'processing', added_at)`; index on `(added_at DESC)`; `filename UNIQUE` so re-ingest of the same name is detectable; `status` values are `processing | complete | failed`; distinct from the Qdrant `documents` collection (vector chunks)
- **Local-path PVC pinning** — k3s `local-path` storage binds the PV to whichever node first schedules a pod that mounts it; for a stateful workload pin the deployment with `nodeSelector: kubernetes.io/hostname: <node>` so the PVC and pod always co-locate. Used for `/data/documents` on vlinux2.
- **BackgroundScheduler alongside FastAPI** — for services that need both an HTTP API and a recurring background job, use `apscheduler.schedulers.background.BackgroundScheduler` (not `BlockingScheduler`) and start it inside a FastAPI `lifespan` context manager. The scheduler runs in its own thread so uvicorn keeps the event loop. Shut it down in the `finally` of the lifespan with `scheduler.shutdown(wait=False)`. Pattern: ingestion service watches `/data/documents` every 5 min while still serving POST `/ingest`.
- **Async ingest pattern** — split heavy work (chunking, Ollama embedding loop, summary call) from the request handler: handler does just the fast catalog-row INSERT and returns, then spawns `threading.Thread(daemon=True)` for the heavy part. Frontend polls the catalog endpoint every few seconds and renders "Processing…" for rows where `chunk_count == 0`. Avoids nginx/axios proxy timeouts on large files without needing a full queue/worker system.
- **Document re-ingest cleanup** — when re-ingesting a file with the same name, you must delete the old Qdrant points OR they linger as orphan chunks. Stamp `document_id` (the catalog row's UUID) into each Qdrant point's payload at ingest time, then on re-ingest or row delete use `qdrant.delete(points_selector=FilterSelector(filter=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=old_id))])))`. Same pattern handles `DELETE /ingest/documents/{id}`.
- **PVC file delete must accompany catalog delete** — when removing a cataloged document, you must also `Path(file_path).unlink()` on the PVC. Leaving the file would cause the folder watcher's next scan to re-ingest it because the filename isn't in the catalog anymore. Three-way cleanup: Qdrant delete-by-filter → catalog row DELETE → file unlink → regenerate TOC.
- **Atomic TOC writes** — the folder watcher and any other readers of `_TABLE_OF_CONTENTS.md` could observe a half-written file if you write in place. Write to `.tmp` then `os.replace()` (atomic on the same filesystem). The watcher also skips `.tmp` extensions and any filename starting with `_` to avoid ingesting its own artifact.
- **Background-job failure visibility** — when a daemon thread does the real work, the request handler has already returned 200 and can't surface failures to the client. Pattern: a `status` column on the catalog row with three states (`processing` default → `complete` on success → `failed` on any error); explicit `_mark_failed(...)` at each known early-return site inside the worker function; outer `try/except Exception` around the whole worker body for uncaught crashes; a separate APScheduler reaper job (10 min interval, 30 min threshold) that flips long-`processing` rows to `failed` to recover from pod restarts that killed the worker without giving it a chance to mark anything failed.
- **App-level health aggregation** — a single endpoint that fan-outs to internal services with short per-check timeouts (2 s) via `httpx.AsyncClient` + `asyncio.gather`, so one dead dependency can't hang the whole view. Treat any non-5xx as "reachable" — a 404 on `/healthz` still proves the service answered a TCP request. Hardcode the self-check (the endpoint can't be answering if we're not reachable). Combine reachability with a Postgres data-snapshot query in the same response so the UI gets everything in one round-trip and can auto-refresh on a single interval.

## What not to do
- Don't suggest cloud-hosted alternatives to self-hosted components
- Don't add Helm charts unless asked
- Don't implement the next phase unless explicitly told to move forward
- Don't restructure the repo layout without asking first