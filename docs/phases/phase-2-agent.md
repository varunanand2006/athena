# Phase 2 — LangGraph Agent Service

## Goal
Deploy a working AI agent reachable at `agent.local` that can answer questions using live web search via SearXNG and local LLM inference via Ollama.

## Phase gate
`POST http://agent.local/chat` with a question about current events returns a response grounded in web search results — not just training knowledge.

---

## What was built

### SearXNG
- Image: searxng/searxng:latest
- Scheduled on xdev-sr (workload=ai)
- Traefik ingress at `searxng.local`
- JSON format enabled, rate limiting disabled (required for API use)
- Config delivered via ConfigMap; init container copies it to an emptyDir because ConfigMap mounts are read-only and SearXNG's entrypoint tries to chown the file
- `enableServiceLinks: false` required — Kubernetes injects `SEARXNG_PORT=tcp://...` which breaks SearXNG's own port parsing

### Agent service (`agent/`)
- FastAPI + LangGraph `create_react_agent`
- Model: `gemma4:e2b` via Ollama (ChatOllama client, no local model weights)
- One tool: `web_search` — calls SearXNG at `searxng.athena.svc.cluster.local:80`
- System prompt instructs the model to always use web_search for current information
- Deployed on xdev-sr (workload=ai), Traefik ingress at `agent.local`
- Built as a Docker image on xdev-sr, imported into k3s via `docker save | k3s ctr images import`

---

## Issues encountered

### SearXNG CrashLoopBackOff
Two bugs hit simultaneously:
1. ConfigMap subPath mounts are read-only — SearXNG's entrypoint can't chown the file. Fixed with an init container that copies the config into an emptyDir.
2. Kubernetes service link env vars: `SEARXNG_PORT=tcp://10.x.x.x:80` was injected, conflicting with SearXNG's own `--port` argument. Fixed with `enableServiceLinks: false`.

### Agent OOM kill (exit code 137)
`agent.invoke()` is synchronous and blocked FastAPI's asyncio event loop during LLM inference. Kubernetes liveness probes couldn't reach `/healthz`, failed 3 times, and sent SIGKILL. Fixed by running `agent.invoke()` in a `ThreadPoolExecutor` via `run_in_executor`, keeping the event loop free. Also raised `failureThreshold` to 10 on the liveness probe.

### Model not using tool
`gemma4:e2b` answered from training knowledge instead of calling `web_search`. Fixed by adding a system prompt that explicitly instructs the model to use the tool for any current information.

---

## Build process (no registry yet)
Images are built with Docker on xdev-sr and imported directly into k3s containerd:
```bash
sudo docker build -t athena-agent:latest agent/
sudo docker save athena-agent:latest | sudo k3s ctr images import -
```
`imagePullPolicy: Never` in the deployment prevents k3s from trying to pull from Docker Hub.

---

## /etc/hosts entries required
Add these on any machine that needs to reach the services:
```
192.168.96.200  searxng.local
192.168.96.200  agent.local
```

---

## Next phase
Phase 3 — ingestion pipeline and memory. LlamaIndex document ingestion into Qdrant, PostgreSQL schema for structured data.
