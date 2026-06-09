# Phase 1 — Cluster Foundation

## Goal
Stand up a working k3s cluster with PostgreSQL, Qdrant, and Ollama deployed and verified. Nothing else. This is the infrastructure layer every other phase depends on.

## Phase gate
`curl http://ollama.local/api/tags` from vlinux1 returns a valid response listing the pulled models.

---

## What was built

### k3s cluster
- k3s v1.35.5+k3s1 installed on all three nodes
- vlinux1 (192.168.96.200) as control plane
- vlinux2 (192.168.96.202) joined as worker with `workload=inference` label
- xdev-sr (192.168.96.201) joined as worker with `workload=ai` label
- Traefik ingress controller included by default (k3s built-in)
- Flannel CNI for pod networking

### PostgreSQL
- Image: postgres:16
- Namespace: athena
- Scheduled on vlinux1 via `nodeSelector: kubernetes.io/hostname: vlinux1`
- 10Gi PVC backed by local-path storage
- Credentials via Kubernetes Secret (gitignored, never committed)
- ClusterIP service at `postgres.athena.svc.cluster.local:5432`

### Qdrant
- Image: qdrant/qdrant:v1.13.6
- Scheduled on xdev-sr via `nodeSelector: workload=ai`
- 10Gi PVC backed by local-path storage
- ClusterIP service + Traefik ingress at `qdrant.local`

### Ollama
- Image: ollama/ollama:latest
- Scheduled on xdev-sr via `nodeSelector: workload=ai`
- 50Gi PVC for model storage
- ClusterIP service + Traefik ingress at `ollama.local`
- Models: `gemma4:e2b` (testing), `nomic-embed-text`

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/install-k3s-master.sh` | Installs k3s server on vlinux1 |
| `scripts/install-k3s-worker.sh` | Installs k3s agent on a worker node |
| `scripts/label-nodes.sh` | Applies workload labels after workers join |
| `scripts/pull-models.sh` | Pulls Ollama models via the API |

---

## Lessons learned
- Worker nodes don't need the repo cloned — just scp the install script or run the curl one-liner inline
- `secret.yaml` must be created manually on vlinux1 from `secret.yaml.example` and never committed
- `/etc/hosts` entries for `.local` hostnames are needed on any machine that wants to reach the ingress
- `gemma4:e2b` (5.12B params, Q4_K_M, 7.2GB) used for Phase 1 testing; swap to `gemma4:12b` for real workloads

---

## Next phase
Phase 2 — Agent & MCP server. LangGraph orchestration service and custom Rust MCP server.
