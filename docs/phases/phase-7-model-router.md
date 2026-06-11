# Phase 7 — Model Router

## Goal
Interactive chat should feel fast. Route `mode=chat` requests to OpenAI GPT-4o-mini (cloud, fast) and keep `mode=background` on gemma4:e2b (local, slow but free). No changes to tools or agent logic — just swap the LLM per request.

## Phase gate
`POST http://agent.local/chat` with `{"message": "hi", "mode": "chat"}` returns a response in under 5 seconds via GPT-4o-mini. Same request with `"mode": "background"` routes to Ollama.

---

## What was built

### Mode-based LLM routing (`agent/main.py`)
`get_llm(mode)` helper selects the LLM per request:
```python
def get_llm(mode: str):
    if mode == "background":
        return ChatOllama(base_url=OLLAMA_BASE_URL, model=OLLAMA_MODEL, temperature=0)
    return ChatOpenAI(model="gpt-4o-mini", api_key=OPENAI_API_KEY, temperature=0)
```
Agent and LangGraph graph are constructed per-request so the LLM swap is seamless. Default mode is `"chat"` (GPT-4o-mini).

### OpenAI secret (`cluster/agent/`)
API key stored as a Kubernetes Secret in the `athena` namespace:
```bash
kubectl create secret generic openai-secret \
  --from-literal=OPENAI_API_KEY=sk-... \
  -n athena
```
Referenced in `cluster/agent/deployment.yaml` as an env var from `secretKeyRef`.

### Dependency additions
- `langchain-openai` added to `agent/pyproject.toml`
- `langchain-openai` added to pip install list in `agent/Dockerfile` (Dockerfile does not use pyproject.toml; must be kept in sync manually)

---

## Issues encountered

### Secret in wrong namespace
Creating the secret without `-n athena` put it in the `default` namespace. Pod started with an empty `OPENAI_API_KEY` rather than failing loudly. Fix: always pass `-n athena` when creating secrets for Athena services.

### `langchain-openai` missing from Dockerfile
Added to `pyproject.toml` but not the `Dockerfile` pip install list. Build succeeded but the container crashed at import. Fix: keep both in sync — `pyproject.toml` for local dev, Dockerfile for the image build.

---

## Build process
```bash
# On xdev-sr
sudo docker build -t athena-agent:latest ~/athena/agent/
sudo docker save athena-agent:latest | sudo k3s ctr images import -

# On vlinux1
kubectl rollout restart deployment/agent -n athena
```

---

## Next phase
Phase 4 (Rust MCP server) — deferred until after Phase 7. Planned: expose internship tracker, LeetCode lookup, and Twilio SMS as MCP tools so they can be called from the agent more cleanly than direct Python functions.
