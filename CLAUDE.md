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
- `agent/` — LangGraph orchestration service (Python)
- `mcp-server/` — Custom MCP server (Rust)
- `ingestion/` — Document ingestion pipelines (LlamaIndex, Python)
- `frontend/` — React web app
- `scripts/` — Setup and utility scripts
- `docs/` — Architecture docs, phase notes, ADRs

## Hardware
- `vlinux1` — 192.168.96.13, 8GB RAM, k3s control plane
- `varun-linux` — 16GB RAM, workload=inference
- `xdev-sr` — 16GB RAM, workload=ai

## Tech stack
- **k3s** with Traefik ingress, Flannel networking
- **Ollama** running Gemma (local inference, CPU only — expect slow responses)
- **LangGraph** for agent orchestration
- **LlamaIndex** for document parsing and ingestion
- **Qdrant** for vector search
- **PostgreSQL** for relational data
- **Rust** for the MCP server
- **React** for the frontend
- **n8n** for scheduled pipelines
- **Twilio** for SMS notifications

## Current phase
Phase 1 — Cluster foundation. Focus is fresh k3s install, PostgreSQL,
Qdrant, and Ollama deployment. Nothing else yet.

## Coding conventions
- Python services use `pyproject.toml`, not `requirements.txt`
- Kubernetes manifests are raw YAML (no Helm unless repo is unavailable)
- Rust code should be idiomatic — use `thiserror`, `tokio`, `axum`
- Commit format: `type(scope): description` — e.g. `feat(agent): add web search tool`
- Never commit secrets, `.env` files, or kubeconfig

## What not to do
- Don't suggest cloud-hosted alternatives to self-hosted components
- Don't add Helm charts unless asked
- Don't implement the next phase unless explicitly told to move forward
- Don't restructure the repo layout without asking first