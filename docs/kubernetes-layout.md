# Athena — Kubernetes Cluster Layout

## Nodes

| Node | IP | RAM | Role | Labels |
|------|----|-----|------|--------|
| vlinux1 | 192.168.96.200 | 8GB | k3s control plane | `node-role.kubernetes.io/control-plane=true` |
| vlinux2 | 192.168.96.202 | 16GB | Worker — lightweight services | `kubernetes.io/hostname=vlinux2`, `workload=inference` |
| xdev-sr | 192.168.96.201 | 16GB | Worker — AI/inference workloads | `kubernetes.io/hostname=xdev-sr`, `workload=ai` |

> Note: the `workload=inference` label on vlinux2 is unused by any manifest. Pod scheduling uses `kubernetes.io/hostname` nodeSelectors directly.

---

## Pod Scheduling

All workloads run in the `athena` namespace.

| Deployment | Node | Image | Reason |
|------------|------|-------|--------|
| postgres | vlinux1 | `postgres:16` | Stable control-plane node; low memory use |
| frontend | vlinux2 | `athena-frontend:latest` | Lightweight nginx; offloads xdev-sr |
| internship-hunter | vlinux2 | `athena-internship:latest` | Lightweight APScheduler poller |
| leetcode | vlinux2 | `athena-leetcode:latest` | Lightweight APScheduler poller |
| agent | xdev-sr | `athena-agent:latest` | Needs direct access to Ollama and Qdrant |
| ingestion | xdev-sr | `athena-ingestion:latest` | Needs direct access to Ollama and Qdrant |
| ollama | xdev-sr | `ollama/ollama:latest` | 16GB RAM needed for model weights |
| qdrant | xdev-sr | `qdrant/qdrant:v1.13.6` | Co-located with agent and ingestion |
| searxng | xdev-sr | `searxng/searxng:latest` | Co-located with agent |

---

## Services (ClusterIP)

All services are ClusterIP — no external exposure, only reachable within the cluster.

| Service | ClusterIP | Port | Selector |
|---------|-----------|------|----------|
| agent | 10.43.186.97 | 80 | `app=agent` |
| frontend | 10.43.217.68 | 80 | `app=frontend` |
| ingestion | 10.43.80.239 | 80 | `app=ingestion` |
| internship-hunter | 10.43.183.85 | 80 | `app=internship-hunter` |
| ollama | 10.43.59.164 | 11434 | `app=ollama` |
| postgres | 10.43.93.195 | 5432 | `app=postgres` |
| qdrant | 10.43.218.138 | 6333, 6334 | `app=qdrant` |
| searxng | 10.43.136.174 | 80 | `app=searxng` |

In-cluster DNS names follow the pattern `<service>.athena.svc.cluster.local:<port>`, e.g. `postgres.athena.svc.cluster.local:5432`.

---

## Ingress (Traefik)

All ingress resources use the `traefik` class and route HTTP on port 80. All hostnames resolve to `192.168.96.200` (Traefik runs on the control plane).

| Hostname | Backing Service | Purpose |
|----------|----------------|---------|
| `athena.local` | frontend | React SPA — main user entry point |
| `agent.local` | agent | LangGraph FastAPI — direct API access |
| `ingest.local` | ingestion | LlamaIndex ingestion API |
| `ollama.local` | ollama | Ollama model API |
| `qdrant.local` | qdrant | Qdrant vector DB HTTP API |
| `searxng.local` | searxng | SearXNG search UI/API |

> `athena.local` (frontend) proxies `/chat`, `/conversations`, `/internships`, `/leetcode`, `/healthz` to the agent ClusterIP via nginx — the browser never hits a different origin.

---

## Persistent Volumes

All volumes use the `local-path` storage class (k3s built-in). Data lives on the node's local disk — no replication.

| PVC | Bound To | Capacity | Node (implied) |
|-----|----------|----------|----------------|
| postgres-pvc | postgres deployment | 10Gi | vlinux1 |
| qdrant-pvc | qdrant deployment | 10Gi | xdev-sr |
| ollama-pvc | ollama deployment | 50Gi | xdev-sr |

> Because local-path PVs are node-local, these pods are effectively pinned to their nodes even without an explicit nodeSelector. If a node is lost, the PV data is lost with it — no cross-node replication.

---

## Network Summary

```
varunlaptop (192.168.96.13) — kubectl, SSH only
      │
      │ HTTP athena.local / agent.local / etc.
      ▼
Traefik (vlinux1, 192.168.96.200) — ingress controller
      │
      ├──► frontend (vlinux2)        nginx + React SPA
      │         │ proxy /chat /conversations /internships /leetcode
      │         ▼
      │    agent (xdev-sr)           LangGraph + FastAPI
      │         ├── ollama (xdev-sr)      gemma4:e2b inference
      │         ├── qdrant (xdev-sr)      vector search
      │         ├── searxng (xdev-sr)     web search
      │         └── postgres (vlinux1)    relational data + chat history
      │
      ├──► ingestion (xdev-sr)       LlamaIndex document pipeline
      │         ├── ollama           embeddings
      │         └── qdrant           vector store
      │
      ├──► internship-hunter (vlinux2)   APScheduler, daily
      │         ├── agent            LLM scoring
      │         └── postgres         internship_postings table
      │
      └──► leetcode (vlinux2)            APScheduler, daily
                └── postgres         leetcode_* tables
```

---

## kubectl Quick Reference

```bash
# Cluster health
kubectl get nodes -o wide
kubectl get pods -n athena -o wide

# Logs
kubectl logs -n athena deployment/<name> --tail=50

# Restart a deployment (run from vlinux1 or varunlaptop — vlinux2 has no kubeconfig)
kubectl rollout restart deployment/<name> -n athena

# Postgres shell
kubectl exec -n athena $(kubectl get pod -n athena -l app=postgres -o jsonpath='{.items[0].metadata.name}') -- psql -U athena -d athena

# Run a one-off SQL statement
kubectl exec -n athena $(kubectl get pod -n athena -l app=postgres -o jsonpath='{.items[0].metadata.name}') -- psql -U athena -d athena -c "<SQL>"
```
