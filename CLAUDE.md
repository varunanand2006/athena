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
Phase 11 — Summary-based RAG. Chunk-level retrieval is gone. The
ingestion pipeline now produces **one Qdrant point per document** —
its vector is the embedding of the gemma4:e2b-generated summary, not
of any chunk. The full extracted document text is cached on the
catalog row in a new `full_text TEXT` column so the agent can load
whole documents back without re-parsing the file from the PVC.
Retrieval at query time is a two-step routing flow:

1. `find_documents(query)` — embed the query via nomic-embed-text,
   search the `documents` Qdrant collection (limit=3), return
   `{document_id, title, summary, score}` for each hit. This picks
   *which* document is relevant; it is not the answer.
2. `load_document(id_or_title)` — return `title + full_text` from the
   Postgres `documents.full_text` cache. The agent answers from this
   full text, not from the summary.

The old `search_documents` tool is removed. The system prompt forbids
answering substantive content questions from the summary alone — load
the full text first.

Ingestion-side, `_embed_and_summarize` now: extracts full text via
`SimpleDirectoryReader` and joins it into one string, generates the
summary (now a **required** artifact — no summary means
unretrievable, so empty summary is a hard `_mark_failed`), embeds the
summary, upserts one Qdrant point with payload
`{document_id, title, summary}`, then UPDATEs the row with
`full_text + summary + chunk_count=1`. All Phase 10 reliability
machinery is preserved: explicit `_mark_failed` at every early-return
site, the outer `try/except` safety net, `_mark_complete` before
`_regenerate_toc` (TOC failure stays cosmetic), and the 30-min reaper
for rows orphaned by pod restarts. `chunk_count` is now vestigial
(always 1 on `complete`, 0 while `processing`); `status` remains the
source of truth.

**Phase 10 context still applies:** the `status` column (`processing |
complete | failed`) drives the frontend Documents view (spinner /
summary / red "Failed" badge with retry-by-delete hint), polling
stops once every row settles, and the agent's `/system/health`
endpoint + `/system` view are unchanged.

**Phase 9 context still applies:** the Postgres `documents` table and
the Qdrant `documents` collection remain different stores. They are
now 1:1 (one catalog row = one Qdrant point) instead of 1:N (one row
= many chunk points), but `document_id` is still stamped into each
point's payload so re-ingest and row-delete continue to use
delete-by-filter cleanly. Original files live on the 10Gi PVC at
`/data/documents` on vlinux2; a `BackgroundScheduler` watches that
folder every 5 min for files dropped in directly.

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
- **Postgres schema — documents** — `documents(id uuid pk, filename text unique, title text, doc_type text, file_path text, summary text, full_text text, chunk_count int, size_bytes int, status text default 'processing', added_at)`; index on `(added_at DESC)`; `filename UNIQUE` so re-ingest of the same name is detectable; `status` values are `processing | complete | failed`; `full_text` caches the extracted document text for the agent's `load_document` tool under summary-routing RAG; `chunk_count` is vestigial post-Phase-11 (always 1 on `complete`, 0 while `processing`) — kept to avoid a destructive migration; distinct from the Qdrant `documents` collection (one summary vector per document)
- **Local-path PVC pinning** — k3s `local-path` storage binds the PV to whichever node first schedules a pod that mounts it; for a stateful workload pin the deployment with `nodeSelector: kubernetes.io/hostname: <node>` so the PVC and pod always co-locate. Used for `/data/documents` on vlinux2.
- **BackgroundScheduler alongside FastAPI** — for services that need both an HTTP API and a recurring background job, use `apscheduler.schedulers.background.BackgroundScheduler` (not `BlockingScheduler`) and start it inside a FastAPI `lifespan` context manager. The scheduler runs in its own thread so uvicorn keeps the event loop. Shut it down in the `finally` of the lifespan with `scheduler.shutdown(wait=False)`. Pattern: ingestion service watches `/data/documents` every 5 min while still serving POST `/ingest`.
- **Async ingest pattern** — split heavy work (text extraction, summary call, embed, Qdrant upsert) from the request handler: handler does just the fast catalog-row INSERT and returns, then spawns `threading.Thread(daemon=True)` for the heavy part. Frontend polls the catalog endpoint every few seconds and renders "Processing…" for rows where `status == 'processing'`. Avoids nginx/axios proxy timeouts on large files without needing a full queue/worker system.
- **Document re-ingest cleanup** — when re-ingesting a file with the same name, you must delete the old Qdrant point(s) OR they linger as orphans. Stamp `document_id` (the catalog row's UUID) into each Qdrant point's payload at ingest time, then on re-ingest or row delete use `qdrant.delete(points_selector=FilterSelector(filter=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=old_id))])))`. Same pattern handles `DELETE /ingest/documents/{id}`. Post-Phase-11 this is a single-point delete per document (one summary vector each), but the filter-by-`document_id` mechanism is unchanged — also covers the pre-Phase-11 multi-chunk case if any old rows linger.
- **PVC file delete must accompany catalog delete** — when removing a cataloged document, you must also `Path(file_path).unlink()` on the PVC. Leaving the file would cause the folder watcher's next scan to re-ingest it because the filename isn't in the catalog anymore. Three-way cleanup: Qdrant delete-by-filter → catalog row DELETE → file unlink → regenerate TOC.
- **Atomic TOC writes** — the folder watcher and any other readers of `_TABLE_OF_CONTENTS.md` could observe a half-written file if you write in place. Write to `.tmp` then `os.replace()` (atomic on the same filesystem). The watcher also skips `.tmp` extensions and any filename starting with `_` to avoid ingesting its own artifact.
- **Background-job failure visibility** — when a daemon thread does the real work, the request handler has already returned 200 and can't surface failures to the client. Pattern: a `status` column on the catalog row with three states (`processing` default → `complete` on success → `failed` on any error); explicit `_mark_failed(...)` at each known early-return site inside the worker function; outer `try/except Exception` around the whole worker body for uncaught crashes; a separate APScheduler reaper job (10 min interval, 30 min threshold) that flips long-`processing` rows to `failed` to recover from pod restarts that killed the worker without giving it a chance to mark anything failed.
- **App-level health aggregation** — a single endpoint that fan-outs to internal services with short per-check timeouts (2 s) via `httpx.AsyncClient` + `asyncio.gather`, so one dead dependency can't hang the whole view. Treat any non-5xx as "reachable" — a 404 on `/healthz` still proves the service answered a TCP request. Hardcode the self-check (the endpoint can't be answering if we're not reachable). Combine reachability with a Postgres data-snapshot query in the same response so the UI gets everything in one round-trip and can auto-refresh on a single interval.
- **Match retrieval architecture to corpus shape** — chunk-level RAG is overkill for a small library of short, organized, text-only documents (class notes, resumes, project writeups). Replacing it with summary-level routing (one vector per document over its summary) plus full-document loading from Postgres on hit is cheaper at ingest (one embed + one upsert per doc instead of N) and gives the LLM strictly more context per hit (whole doc, not a 512-token chunk). Tradeoff: weak on very long documents (entire doc must fit in the LLM context), and the summary becomes a **required** ingest artifact because it IS the retrieval key — empty summary must be a hard `_mark_failed`, not a partial success.
- **Agent two-step retrieval (route, then load)** — keep "find the right document" and "read its content" as separate tools (`find_documents` + `load_document`), not one fused "search" tool. The system prompt then says explicitly: never answer substantive content questions from the summary returned by the routing step — always call `load_document` on the top hit and answer from full text. Two clear tools the LLM can reason about beat one ambiguous tool whose output looks like an answer but isn't one.

## What not to do
- Don't suggest cloud-hosted alternatives to self-hosted components
- Don't add Helm charts unless asked
- Don't implement the next phase unless explicitly told to move forward
- Don't restructure the repo layout without asking first