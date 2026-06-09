# Athena — System Architecture

## What it is
Athena is a self-hosted AI assistant running on a bare-metal k3s cluster. It acts as a background brain — monitoring email, tracking internship applications, surfacing relevant research, and handling routine tasks via natural language. Think JARVIS: proactive, persistent, and entirely local.

---

## Hardware

| Node | IP | RAM | Role |
|------|----|-----|------|
| vlinux1 | 192.168.96.200 | 8GB | k3s control plane, PostgreSQL |
| vlinux2 | 192.168.96.202 | 16GB | workload=inference |
| xdev-sr | 192.168.96.201 | 16GB | workload=ai (Qdrant, Ollama) |
| varunlaptop | 192.168.96.13 | — | Personal laptop, not a cluster node |

All inference is CPU-only. No GPUs.

---

## Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Orchestration | k3s + Traefik | Kubernetes cluster, ingress routing |
| Agent | LangGraph (Python) | Multi-step reasoning, tool orchestration |
| MCP server | Rust (axum, tokio) | Custom tool definitions exposed to the agent |
| Inference | Ollama (gemma4:e2b → gemma4:12b) | Local LLM, no cloud dependency |
| Embeddings | Ollama (nomic-embed-text) | Text → vector conversion for semantic search |
| Vector store | Qdrant | Semantic search over documents and memories |
| Relational DB | PostgreSQL 16 | Structured data: tasks, applications, events |
| Ingestion | LlamaIndex (Python) | Document parsing and chunking pipelines |
| Pipelines | n8n | Scheduled automations (email polling, digests) |
| Notifications | Twilio | SMS alerts for important events |
| Frontend | React | Web UI for interacting with the assistant |

---

## Data Flow

```
External sources (Gmail, web, files)
        │
        ▼
   n8n pipelines  ──────────────────────────────────────────┐
        │                                                    │
        ▼                                                    ▼
LlamaIndex ingestion                               PostgreSQL (structured)
        │
        ▼
nomic-embed-text  →  Qdrant (vector store)
                            │
                            │  semantic search
                            ▼
User query  ──►  LangGraph agent  ──►  MCP server (tools)
                       │
                       ▼
                 gemma4 (Ollama)
                       │
                       ▼
              Response → React UI / Twilio SMS
```

---

## Networking

- All services run in the `athena` namespace
- Inter-service communication via ClusterIP (e.g. `postgres.athena.svc.cluster.local:5432`)
- External access via Traefik ingress:
  - `qdrant.local` → Qdrant HTTP API
  - `ollama.local` → Ollama API
  - `athena.local` → React frontend (Phase 3+)
- `.local` hostnames require `/etc/hosts` entries pointing to `192.168.96.200`

---

## Node Scheduling

| Workload | Node | Reason |
|----------|------|--------|
| PostgreSQL | vlinux1 | Control plane node, low memory use, stable |
| Qdrant | xdev-sr (workload=ai) | Vector ops benefit from dedicated resources |
| Ollama | xdev-sr (workload=ai) | 16GB RAM needed for model weights |
| Agent/ingestion | vlinux2 (workload=inference) | CPU-bound Python workloads |
