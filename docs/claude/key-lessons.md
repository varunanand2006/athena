# Key Lessons

Hard-won implementation lessons grouped by topic. Always relevant — don't skip.

---

## Cluster ops & image builds

- **Image build workflow** — docker is on xdev-sr; build there: `sudo docker build`, `sudo docker save -o /tmp/image.tar`, `sudo chmod 644`. Import must happen on the node where the pod runs (`kubectl get pods -o wide`): agent/ollama/qdrant/searxng → import on xdev-sr directly; frontend/internship/leetcode/ingestion → pull via reverse SCP (`scp ubuntu@192.168.96.201:/tmp/image.tar /tmp/`) then import on vlinux2. `kubectl rollout restart` always from vlinux1 or laptop (vlinux2 has no kubeconfig). /tmp is wiped on reboot — rebuild if the tar is gone. **Skip gzip** — `docker save | gzip` hangs on CPU-constrained xdev-sr; use `docker save -o file.tar` only.

- **Import with `k3s ctr`, NOT plain `ctr`** — k3s runs its own embedded containerd (socket `/run/k3s/containerd/containerd.sock`), separate from system containerd. The import command MUST be `sudo k3s ctr images import /tmp/image.tar`. **Failure signature:** importing with plain `sudo ctr -n k8s.io images import` makes the image appear in `sudo ctr -n k8s.io images list` — but it lands in the wrong containerd, kubelet never sees it, and the pod fails `ErrImageNeverPull` even though "the image is right there." Verify with `sudo k3s ctr images list | grep <name>`, not plain `ctr`. Burned ~40 min in Phase 15.

- **Image tag workflow** — cluster deploys pin per-phase tags (`:phaseN`), not `:latest`. Workflow: build → re-tag → import → bump the YAML tag → `kubectl apply`. Never rely on `:latest` for rollout determinism.

- **Repo paths per machine** — checkout is `~/athena` on xdev-sr but `~/projects/athena` on vlinux1. A wrong-dir build mis-tagged the agent as the frontend. Always verify `Config.Cmd` after building to confirm you built the right service.

- **kubectl exec stdin** — piping SQL via `< file` through `kubectl exec` is unreliable. Prefer `kubectl cp` then `psql -f`. If the SQL file is only on the dev machine, pass it inline with `psql -c "..."` to skip the copy step entirely.

- **Local-path PVC pinning** — k3s `local-path` storage binds the PV to whichever node first schedules a pod that mounts it. For stateful workloads pin with `nodeSelector: kubernetes.io/hostname: <node>` so the PVC and pod always co-locate. Used for `/data/documents` on vlinux2 and `/data/memory` on xdev-sr.

- **Pinning pods to vlinux2** — use `nodeSelector: kubernetes.io/hostname: vlinux2`, not a workload label. The existing `workload=inference` label on vlinux2 is unused by any manifest. Ingress host `athena.local` points to 192.168.96.200 (control plane, where Traefik runs).

- **k3s cluster survives hard power cuts** — all three nodes came back cleanly after a full power outage; k3s and all pods auto-restarted with exactly 1 restart each; Postgres data survived intact. No manual recovery needed — the only follow-up was running a pending schema migration.

- **rmcp allowed_hosts gotcha** — rmcp `StreamableHttpService` 403s non-loopback Host headers by default. Add ingress/tunnel hostnames to the `ALLOWED_HOSTS` env. Also explains Claude Code's sticky "Needs authentication" badge.

---

## k8s secrets & environment

- **k8s secrets namespace** — always create secrets with `-n athena`. A secret in the default namespace is invisible to pods in the athena namespace — the pod starts with an empty env var rather than failing loudly.

- **k8s secrets for Google APIs** — Gmail uses `gmail-secret` (keys: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`); Calendar uses `gcal-secret` (keys: `GCAL_CLIENT_ID`, `GCAL_CLIENT_SECRET`, `GCAL_REFRESH_TOKEN`). Both in the **athena** namespace, wired `optional: true` so the agent starts before the secrets exist (tools report "not configured" until then).

---

## Python service patterns

- **APScheduler pattern** — use `BlockingScheduler` from `apscheduler.schedulers.blocking` for standalone polling services. Run the pipeline once on startup before handing off to the scheduler so the first deploy is immediately testable.

- **BackgroundScheduler alongside FastAPI** — for services that need both an HTTP API and a recurring background job, use `apscheduler.schedulers.background.BackgroundScheduler` (not `BlockingScheduler`) started inside a FastAPI `lifespan` context manager. The scheduler runs in its own thread so uvicorn keeps the event loop. Shut it down in the `finally` with `scheduler.shutdown(wait=False)`.

- **Async ingest pattern** — split heavy work (text extraction, summary call, embed, Qdrant upsert) from the request handler: handler does just a fast catalog-row INSERT and returns 200, then spawns `threading.Thread(daemon=True)` for the heavy part. Frontend polls the catalog endpoint and renders "Processing…" for `status == 'processing'` rows.

- **Background-job failure visibility** — when a daemon thread does the real work, the handler has already returned 200. Pattern: `status` column with three states (`processing` → `complete` → `failed`); explicit `_mark_failed(...)` at each early-return site; outer `try/except Exception` around the whole worker body; an APScheduler reaper job (10 min interval, 30 min threshold) that flips long-`processing` rows to `failed` to recover from pod restarts.

- **App-level health aggregation** — single endpoint that fans out to internal services with 2s per-check timeouts via `httpx.AsyncClient` + `asyncio.gather`. Treat any non-5xx as "reachable." Hardcode the self-check. Combine reachability with a Postgres data-snapshot in the same response so the UI gets everything in one round-trip.

- **Mode-based model routing** — `POST /chat` accepts an optional `mode` field (`"chat"` → GPT-4o-mini, `"background"` → Gemma via Ollama). Agent and LangGraph graph are constructed per-request so the LLM swap is seamless.

- **langchain-openai sync** — add to both `pyproject.toml` AND `agent/Dockerfile` pip install list. The Dockerfile doesn't use pyproject.toml, so they must be kept in sync manually.

- **Multi-chat history** — pass full conversation history as the `messages` array to `create_react_agent`. Load it from Postgres ordered by `created_at ASC` before every /chat call.

---

## Database / PostgreSQL

- **UUID primary keys** — use `gen_random_uuid()` as the default for UUID PKs in Postgres. Returns a `uuid` type — cast to `str` in Python before returning in JSON.

- **Postgres schema — conversations/messages** — `conversations(id uuid pk, title text, created_at, updated_at, reflected_at timestamptz default null)`; `messages(id uuid pk, conversation_id uuid fk→conversations, role text, content text, created_at)`; index on `messages(conversation_id)`. `reflected_at` is Phase 15's watermark; a conversation is DUE when `reflected_at IS NULL OR updated_at > reflected_at`.

- **Postgres schema — documents** — `documents(id uuid pk, filename text unique, title text, doc_type text, file_path text, summary text, full_text text, chunk_count int, size_bytes int, status text default 'processing', added_at)`; index on `(added_at DESC)`. `filename UNIQUE` makes re-ingest detectable. `status` values: `processing | complete | failed`. `full_text` caches extracted text for `load_document`. `chunk_count` is vestigial (always 1 on complete) — kept to avoid a destructive migration.

- **Document re-ingest cleanup** — when re-ingesting a file with the same name, delete old Qdrant points or they linger as orphans. Stamp `document_id` into each point's payload at ingest time, then on re-ingest or delete use `qdrant.delete` with `FilterSelector` by `document_id`.

- **PVC file delete must accompany catalog delete** — when removing a cataloged document, also `Path(file_path).unlink()`. Leaving the file causes the folder watcher's next scan to re-ingest it since the filename is no longer in the catalog. Three-way cleanup: Qdrant delete-by-filter → catalog row DELETE → file unlink → regenerate TOC.

- **Atomic TOC writes** — write to `.tmp` then `os.replace()` (atomic on the same filesystem). The watcher also skips `.tmp` extensions and filenames starting with `_` to avoid ingesting its own artifact.

---

## Frontend / nginx

- **React/Vite multi-stage build** — Node 20 builder stage runs `npm ci && npm run build`; nginx:alpine serves `dist/`. Keep `nginx.conf` next to the Dockerfile so the COPY path is predictable.

- **Nginx CORS proxy for SPA** — proxy `/chat`, `/internships`, `/leetcode` to the agent ClusterIP so the browser never hits a different origin. Set `proxy_read_timeout 120s` for slow LLM responses.

---

## Agent & models

- **gemma4:e2b is a thinking model** — raw `/api/generate` returns an empty `response` because all tokens are consumed by internal reasoning. Use `/api/chat` with `"think": false` for structured output tasks. Read from `message.content`, not `response`.

- **Ollama token limits for CPU inference** — always pass `num_ctx: 2048, num_predict: 150` to keep responses fast on CPU. Set httpx timeouts to 90s per call.

- **Match retrieval architecture to corpus shape** — chunk-level RAG is overkill for a small library of short documents. Summary-level routing (one vector per document) + full-document load from Postgres is cheaper at ingest and gives the LLM strictly more context. Tradeoff: weak on very long documents; the summary becomes a **required** ingest artifact — empty summary must be a hard `_mark_failed`, not a partial success.

- **Agent two-step retrieval** — keep "find the right document" and "read its content" as separate tools (`find_documents` + `load_document`), not one fused search. System prompt: never answer substantive questions from the summary — always call `load_document` and answer from full text.

---

## Memory system

- **Memory vault note format** — every note is a markdown file with YAML frontmatter: `title`, `created` (ISO date), `updated` (bumped on every write), `source: explicit|auto`, `tags: []`, `events: []`. Filename = slugified title + `.md` — the slug is the note's identity, same-title writes UPDATE in place. `source` is preserved on update (auto-reflection touching a user note keeps it `explicit`). `events` is the only structured queryable field (optional `{date: YYYY-MM-DD, kind}` list); merged across same-slug updates. Format + `slugify()` + `assemble_memory_context()` + `collect_events()` live in `agent/memory.py`.

- **Automatic memory capture (Phase 15)** — reflection is triggered at the new-conversation boundary via the watermark pattern (`reflected_at IS NULL OR updated_at > reflected_at`). Runs in background threads, never blocks /chat. A 30-min APScheduler straggler sweep catches missed conversations. Foreground chat agent stays **explicit-only** — `write_memory` fires only on an explicit "remember" instruction. Re-verify this constraint on any foreground-model swap.

- **Ambient memory recall (Phase 16)** — the whole vault is loaded into the system prompt each turn (NOT a retrieval system). Token measurement uses char/4 heuristic (deliberately NOT tiktoken). `MEMORY_CONTEXT_MAX_TOKENS` (default 8000) is the named tripwire. Inject via system prompt, NOT a user-turn prefix — a prefix would enter the Postgres message record and pollute history. Re-verify `RECALL_POLICY` on any foreground-model swap.

- **Temporal frontmatter (Phase 17)** — dated notes carry `events: [{date: YYYY-MM-DD, kind}]` frontmatter. No separate events table — the note is the one record. Reflection emits ONLY concrete resolved dates; `_sanitize_events` drops anything non-ISO. `upcoming(timeframe)` does a full-vault events scan with a `MEMORY_EVENTS_MAX_NOTES` tripwire (default 500).

- **Interlinked memory / wiki graph (Phase 18)** — notes cross-link with `[[wikilinks]]`. Graph is **derived from prose** — no link table. Link identity = the slug. Concept pages (`concept: true` frontmatter) are **reconciled** via `write_note(replace=True)`; ordinary notes keep the Phase 15 append. `_index.md` + `_log.md` are generated artifacts, `_`-prefixed so `list_notes()` skips them. `GET /memory/graph` is declared BEFORE `/memory/{slug}` so "graph" isn't treated as a slug.

---

## MCP server

- **Tool registry pattern (Phase 12)** — the Rust MCP server is a thin proxy. Each tool is a `ToolDefinition { name, description, input_schema, agent_path, method, capability }` in `mcp-server/src/registry.rs`; one generic forwarder in `agent_client.rs` proxies it to the agent's `/tools/<name>` endpoint. Adding a tool is a DATA change: append one `ToolDefinition` + add the matching agent endpoint. The proxy wraps any non-object agent response under a `result` key — MCP requires `structuredContent` to be a JSON object, but tools like `find_documents` return a top-level array.

- **MCP server is LAN-only until Phase 13** — no auth exists on the MCP server before Phase 13. The server MUST NOT be exposed beyond the LAN (no Cloudflare Tunnel) until then. The middleware seam is the `mcp_routes` Router in `mcp-server/src/main.rs`. Bearer-token auth (Phase 13) is authentication only — it does NOT gate on capability (the tool name is in the JSON-RPC body, invisible at the HTTP layer). Laptop registration: `claude mcp add --transport http athena --scope user http://mcp.local/mcp`.

---

## Google APIs

- **Gmail read-only lookup (Phase 19)** — `search_email(query)` in `agent/main.py` calls `agent/gmail_client.py`. Returns a ≤10-message digest (sender/subject/date/snippet). Read-only enforced at the **credential**: OAuth scope is `gmail.readonly` only, hardcoded in both `gmail_client.py` and `scripts/gmail_oauth.py`. Mint the refresh token once locally with `scripts/gmail_oauth.py /path/to/client_secret.json` (needs `google-auth-oauthlib`, a laptop-only dep — NOT in the agent image). NOT exposed via the Rust MCP server.

- **Google Calendar read-only lookup (Phase 20)** — `get_calendar_events(timeframe)` in `agent/main.py` calls `agent/calendar_client.py`. Returns a ≤10-event digest (title/start/end/location) for a natural-language timeframe. OAuth scope is `calendar.readonly` only. Same Desktop app OAuth client can be reused for both Gmail and Calendar (scopes are requested at flow time). Mint refresh token with `scripts/calendar_oauth.py`. NOT exposed via the Rust MCP server.
