# ADR 002 — MCP Server in Rust

**Date:** 2026-06-08  
**Status:** Accepted

---

## Context
Athena's agent (LangGraph) needs a way to expose custom tools — actions like querying the internship tracker, sending SMS via Twilio, searching Qdrant, or triggering n8n workflows. The Model Context Protocol (MCP) is the standard interface for this.

The MCP server is a long-running HTTP service. It needs to be:
- Fast and low-latency (tool calls are in the hot path of every agent response)
- Memory-efficient (running on a cluster with constrained RAM)
- Reliable (a crash here stalls the entire agent)

---

## Decision
Implement the MCP server in **Rust** using `axum` (HTTP), `tokio` (async runtime), and `thiserror` (error handling).

---

## Why Rust
- **Performance:** Near-zero overhead per request. Tool calls happen on every agent turn; latency compounds.
- **Memory:** Rust services typically run in <50MB RSS. A Python FastAPI equivalent sits at 150–300MB at idle.
- **Reliability:** The ownership model eliminates entire classes of runtime bugs (null dereferences, data races). A robust MCP server means fewer agent stalls.
- **Binary deployment:** Single static binary, no runtime dependencies, trivial to containerize with a scratch or distroless base image.
- **Familiarity context:** Rust is the chosen language for this component — it's not being introduced speculatively.

## Why axum specifically
- Ergonomic, composable, built on tokio (the de facto async runtime)
- Excellent compile-time guarantees on handler signatures
- Well-maintained by the tokio team

---

## Alternatives considered

| Option | Rejected because |
|--------|-----------------|
| Python (FastAPI) | Higher memory, slower cold start, runtime errors possible |
| Go | Reasonable alternative, but Rust was already chosen for this project |
| Node.js | Poor fit for a systems-adjacent service with strict resource constraints |
| Existing MCP SDKs | May not cover all custom tools needed; owning the implementation gives full control |

---

## Consequences
- MCP server must be compiled before deployment (CI builds the binary and packages into a container)
- Rust compile times are longer than interpreted languages — local dev iteration is slower
- Contributors need Rust familiarity to modify tool definitions
- Error handling must be explicit — `thiserror` for library errors, `anyhow` acceptable in binary entry points
