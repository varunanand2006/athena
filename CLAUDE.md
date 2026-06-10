# Athena — Claude Code Context

## What this project is
Athena is a self-hosted AI assistant running on a bare-metal k3s cluster.
It helps with internship tracking, email monitoring, LeetCode prep, research,
and personal knowledge management. Think JARVIS — a background brain that
surfaces relevant information and handles routine tasks.

## Planning vs building
**All architecture decisions and phase planning happen in Claude.ai chat.**
**All implementation work happens here in Claude Code (or Google Antigravity).**

If you're unsure whether something is a planning question or an implementation
question, err toward implementing and noting any assumptions made.

## Repo structure
- `cluster/` — Kubernetes manifests (k3s, Traefik, node configs)
- `agent/` — LangGraph orchestration service (Python); interactive chat uses GPT-4o-mini, background tasks use gemma4:e2b
- `mcp-server/` — Custom MCP server (Rust)
- `ingestion/` — Document ingestion pipelines (LlamaIndex, Python)
- `internship/` — Internship hunter service (APScheduler, daily pipeline)
- `frontend/` — React web app (Vite + TypeScript + Tailwind); served by nginx on vlinux2, proxies /chat /internships /leetcode to the agent
- `scripts/` — Setup and utility scripts
- `docs/` — Architecture docs, phase notes, ADRs

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
Phase 7 — Model Router. Agent routes chat requests to GPT-4o-mini (fast, cloud)
and background pipeline calls to gemma4:e2b (local, Ollama). Mode is set via the
`mode` field on POST /chat — defaults to "chat". OpenAI key stored in
`openai-secret` k8s secret in the athena namespace.

## Coding conventions
- Python services use `pyproject.toml`, not `requirements.txt`
- Kubernetes manifests are raw YAML (no Helm unless repo is unavailable)
- Rust code should be idiomatic — use `thiserror`, `tokio`, `axum`
- Commit format: `type(scope): description` — e.g. `feat(agent): add web search tool`
- Never commit secrets, `.env` files, or kubeconfig

## Key lessons
- **APScheduler pattern** — use `BlockingScheduler` from `apscheduler.schedulers.blocking` for polling services; run the pipeline once on startup before handing off to the scheduler so the first deploy is immediately testable
- **Ollama token limits for CPU inference** — always pass `num_ctx: 2048, num_predict: 150` to keep responses fast on CPU; set httpx timeouts to 90s per call
- **Image build workflow** — docker is on xdev-sr; build there, `docker save`, scp the tar to the target node, `sudo k3s ctr images import`; use `sudo chmod 644` on the tar before scp if saved with sudo
- **kubectl exec stdin** — piping SQL via `< file` through `kubectl exec` is unreliable; use `kubectl cp` to copy the file into the pod then run `psql -f`
- **React/Vite multi-stage build** — Node 20 builder stage runs `npm ci && npm run build`; nginx:alpine serves `dist/`; keep `nginx.conf` next to the Dockerfile so the COPY path is predictable
- **Nginx CORS proxy for SPA** — proxy `/chat`, `/internships`, `/leetcode` to the agent ClusterIP so the browser never hits a different origin; set `proxy_read_timeout 120s` for slow LLM responses
- **Pinning pods to vlinux2** — use `nodeSelector: kubernetes.io/hostname: vlinux2`, not a workload label; the existing `workload=inference` label on vlinux2 is unused by any manifest; ingress host `athena.local` points to 192.168.96.200 (control plane, where Traefik runs)
- **Mode-based model routing** — POST /chat accepts an optional `mode` field ("chat" → GPT-4o-mini, "background" → Gemma via Ollama); agent and LangGraph graph are constructed per-request so the LLM swap is seamless
- **langchain-openai** — add to both `pyproject.toml` AND `agent/Dockerfile` pip install list; the Dockerfile doesn't use pyproject.toml so they must be kept in sync manually
- **k8s secrets** — always create secrets with `-n athena`; a secret in the default namespace is invisible to pods in the athena namespace and the pod will start with an empty env var rather than failing loudly

## What not to do
- Don't suggest cloud-hosted alternatives to self-hosted components
- Don't add Helm charts unless asked
- Don't implement the next phase unless explicitly told to move forward
- Don't restructure the repo layout without asking first