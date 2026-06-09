# Athena

> A self-hosted AI assistant for internship tracking, research, and personal knowledge.
> Running on a bare-metal k3s cluster with a local Gemma model, custom Rust MCP server,
> and a LangGraph orchestration layer.

## Status
🟡 Phase 1 in progress — cluster foundation

## Architecture
*Diagram coming in Phase 1*

## Stack
| Component | Technology | Why |
|---|---|---|
| Orchestration | k3s | Lightweight, bundles Traefik |
| AI Orchestration | LangGraph | Stateful agent loops, tool routing |
| Local Model | Gemma (Ollama) | Air-gapped inference, swappable |
| Vector DB | Qdrant | Fast semantic search, self-hosted |
| Relational DB | PostgreSQL | Structured tracking data |
| MCP Server | Rust | Performance, correctness, learning goal |
| Document Parsing | LlamaIndex | Best-in-class RAG pipeline |
| Frontend | React | Custom UI, daily driver |
| Notifications | Twilio | SMS alerts |
| Automation | n8n | Cron scheduling, pipelines |

## Phases
- [ ] Phase 1 — Cluster foundation
- [ ] Phase 2 — LangGraph agent (minimal)
- [ ] Phase 3 — RAG pipeline
- [ ] Phase 4 — Rust MCP server v1
- [ ] Phase 5 — Gmail integration
- [ ] Phase 6 — LeetCode + application tracker
- [ ] Phase 7 — Frontend
- [ ] Phase 8 — Notifications + daily digest
- [ ] Phase 9 — Polish & MCP expansion

## Docs
- [Architecture](docs/architecture.md)
- [Phase Notes](docs/phases/)
- [Architecture Decision Records](docs/adr/)

## Running Athena
*Setup instructions added per phase*