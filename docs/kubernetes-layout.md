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

## Operations

### SSH access

All nodes are accessible by hostname from within the local network.

```bash
ssh ubuntu@192.168.96.200   # vlinux1
ssh ubuntu@192.168.96.201   # xdev-sr
ssh ubuntu@192.168.96.202   # vlinux2
# or by hostname if /etc/hosts is configured
ssh ubuntu@vlinux1
ssh ubuntu@xdev-sr
ssh ubuntu@vlinux2
```

> vlinux2 uses password auth from xdev-sr (SSH key not set up between them). Pull files from vlinux2 using reverse SCP instead of pushing — see image transfer section below.

---

### Running kubectl

**kubectl is only available on vlinux1 and varunlaptop.** vlinux2 and xdev-sr have no kubeconfig and will fail with "connection refused on localhost:8080" if you try to run kubectl there.

Always run `kubectl rollout restart` and other control-plane operations from vlinux1 or your laptop.

```bash
# Check what's running
kubectl get nodes -o wide
kubectl get pods -n athena -o wide          # shows which node each pod is on
kubectl get pods -n athena                  # quick status + restart count

# Logs
kubectl logs -n athena deployment/<name> --tail=50
kubectl logs -n athena deployment/<name> -f          # follow

# Describe a pod (useful for crash debugging)
kubectl describe pod -n athena <pod-name>

# Restart a deployment (picks up a newly imported image)
kubectl rollout restart deployment/<name> -n athena
kubectl rollout status deployment/<name> -n athena   # wait for rollout to complete

# Apply a manifest change
kubectl apply -f cluster/<service>/deployment.yaml

# Create a secret (always pass -n athena — secrets in default namespace are invisible to pods)
kubectl create secret generic <name> --from-literal=KEY=value -n athena
```

---

### Building and deploying images

Docker is installed on **xdev-sr only**. All custom image builds happen there.

#### Step 1 — Build on xdev-sr

```bash
# Always use sudo — ubuntu is not in the docker group by default
sudo docker build -t athena-agent:latest agent/
sudo docker build -t athena-frontend:latest frontend/
sudo docker build -t athena-ingestion:latest ingestion/
sudo docker build -t athena-internship:latest internship/
sudo docker build -t athena-leetcode:latest leetcode/
```

#### Step 2 — Save to a tar

```bash
sudo docker save athena-agent:latest | gzip > /tmp/athena-agent.tar.gz
sudo chmod 644 /tmp/athena-agent.tar.gz
```

> `/tmp` is wiped on reboot. If a node has been restarted since the last build, the tar is gone — rebuild.

#### Step 3 — Import on the correct node

The image must be imported on the **node where the pod runs**. Check with `kubectl get pods -n athena -o wide` first.

| Pod | Runs on | Import method |
|-----|---------|--------------|
| agent, ingestion, ollama, qdrant, searxng | xdev-sr | Import locally — no transfer needed |
| frontend, internship-hunter, leetcode | vlinux2 | Transfer from xdev-sr first (see below) |
| postgres | vlinux1 | Upstream image — no custom build |

**Importing locally on xdev-sr:**
```bash
sudo k3s ctr images import /tmp/athena-agent.tar.gz
```

**Transferring to vlinux2 (reverse SCP — pull from vlinux2, don't push from xdev-sr):**
```bash
# On vlinux2
scp ubuntu@192.168.96.201:/tmp/athena-frontend.tar.gz /tmp/
sudo k3s ctr images import /tmp/athena-frontend.tar.gz
```

> Push direction (xdev-sr → vlinux2) fails due to missing SSH key auth. Always pull from vlinux2 instead.

#### Step 4 — Restart the deployment

```bash
# From vlinux1 or varunlaptop
kubectl rollout restart deployment/agent -n athena
kubectl rollout status deployment/agent -n athena
```

#### Full example — deploying the agent

```bash
# On xdev-sr
git pull
sudo docker build -t athena-agent:latest agent/
sudo docker save athena-agent:latest | gzip > /tmp/athena-agent.tar.gz
sudo chmod 644 /tmp/athena-agent.tar.gz
sudo k3s ctr images import /tmp/athena-agent.tar.gz

# On vlinux1 or varunlaptop
kubectl rollout restart deployment/agent -n athena
kubectl rollout status deployment/agent -n athena
```

#### Full example — deploying the frontend

```bash
# On xdev-sr
git pull
sudo docker build -t athena-frontend:latest frontend/
sudo docker save athena-frontend:latest | gzip > /tmp/athena-frontend.tar.gz
sudo chmod 644 /tmp/athena-frontend.tar.gz

# On vlinux2
scp ubuntu@192.168.96.201:/tmp/athena-frontend.tar.gz /tmp/
sudo k3s ctr images import /tmp/athena-frontend.tar.gz

# On vlinux1 or varunlaptop
kubectl rollout restart deployment/frontend -n athena
kubectl rollout status deployment/frontend -n athena
```

---

### Postgres operations

```bash
# Interactive psql shell
kubectl exec -it -n athena \
  $(kubectl get pod -n athena -l app=postgres -o jsonpath='{.items[0].metadata.name}') \
  -- psql -U athena -d athena

# Run a single SQL statement
kubectl exec -n athena \
  $(kubectl get pod -n athena -l app=postgres -o jsonpath='{.items[0].metadata.name}') \
  -- psql -U athena -d athena -c "SELECT count(*) FROM conversations;"

# List all tables
kubectl exec -n athena \
  $(kubectl get pod -n athena -l app=postgres -o jsonpath='{.items[0].metadata.name}') \
  -- psql -U athena -d athena -c '\dt'

# Run a migration file (copy it into the pod first)
kubectl cp scripts/migrate.sql \
  athena/$(kubectl get pod -n athena -l app=postgres -o jsonpath='{.items[0].metadata.name}'):/tmp/migrate.sql \
  -n athena
kubectl exec -n athena \
  $(kubectl get pod -n athena -l app=postgres -o jsonpath='{.items[0].metadata.name}') \
  -- psql -U athena -d athena -f /tmp/migrate.sql
```

> If the migration file only exists on the dev machine (not on the cluster), use `-c "..."` inline rather than `kubectl cp` + `-f`.

---

### Post-power-outage checklist

The cluster recovers automatically after a hard power cut — no manual intervention needed for the cluster itself.

```bash
# 1. Verify all nodes are Ready
kubectl get nodes

# 2. Verify all pods are Running (expect RESTARTS=1, that's normal)
kubectl get pods -n athena

# 3. Verify Postgres is intact
kubectl exec -n athena \
  $(kubectl get pod -n athena -l app=postgres -o jsonpath='{.items[0].metadata.name}') \
  -- psql -U athena -d athena -c '\dt'

# 4. Apply any pending migrations that hadn't been run before the outage
```

> `/tmp` is cleared on reboot — any image tars saved there before the outage are gone and must be rebuilt.
