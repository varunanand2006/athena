# ADR 001 — Database Choice: PostgreSQL + Qdrant

**Date:** 2026-06-08  
**Status:** Accepted

---

## Context
Athena needs to store two fundamentally different kinds of data:

1. **Structured data** — internship applications, tasks, calendar events, user preferences. Rows with known schemas, foreign keys, and the need for transactional updates.
2. **Semantic data** — document chunks, email summaries, research snippets. Retrieved not by ID but by meaning ("find things similar to this query").

These access patterns are incompatible. A relational database is a poor vector store, and a vector database is a poor place for structured records.

---

## Decision
Use **PostgreSQL** for relational data and **Qdrant** for vector search. Run both self-hosted on the k3s cluster.

---

## PostgreSQL — why
- Battle-tested, well-understood, excellent tooling
- Handles everything structured: applications tracker, task queue, event log
- Native JSON support covers semi-structured payloads without needing a separate document store
- LangGraph can use it as a checkpointer for agent state persistence
- Fits comfortably on vlinux1 (8GB RAM) given expected data volumes

## Qdrant — why
- Purpose-built for vector similarity search, significantly faster than pgvector at scale
- Written in Rust — low memory overhead, good performance on CPU-only hardware
- Clean REST + gRPC API, straightforward LlamaIndex and LangGraph integration
- Supports payload filtering (combine semantic search with metadata conditions)
- Active development, production-ready as of v1.x

---

## Alternatives considered

| Option | Rejected because |
|--------|-----------------|
| pgvector (PostgreSQL extension) | Simpler ops, but slower similarity search and less feature-rich than Qdrant at scale |
| ChromaDB | Python-native but less performant and not production-hardened |
| Weaviate | Feature-rich but heavier resource footprint |
| SQLite | Not suitable for a networked multi-service architecture |
| MongoDB | Adds a third data layer with no clear advantage over PostgreSQL + Qdrant |

---

## Consequences
- Two databases to operate and back up
- Services must know which store to use (LangGraph → PostgreSQL for state, LlamaIndex → Qdrant for document retrieval)
- Both are stateful and require persistent volumes — node failure means data loss without backups (Phase 4+ concern)
