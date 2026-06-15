# ADR 005 — MCP Server as Thin Proxy

**Date:** 2026-06-14
**Status:** Accepted

---

## Context
[ADR 002](002-rust-mcp-server.md) accepted Rust + axum as the language
and HTTP framework for Athena's MCP server but deferred the
*architecture*: where does tool logic live, how do new tools get added,
and how do auth and transport work as the deployment grows?

By Phase 12 there were three retrieval tools already implemented inside
the agent's LangGraph agent as `@tool` functions (`find_documents`,
`load_document`, `lookup_leetcode`), each with non-trivial logic
(Qdrant search, Postgres reads, summary-routing rules). They needed to
become callable from Claude Code on the laptop over the LAN, with a
clear path to LAN+tunnel exposure (Phase 13) and to write-capable tools
later.

Three structural choices needed deciding before writing code:

1. **Where does tool logic live?** Duplicate inside the MCP server, or
   keep in the agent and proxy?
2. **How are tools added?** Per-tool handler functions, or a data-driven
   registry?
3. **What transport?** stdio, HTTP-only, SSE, or streamable HTTP?

---

## Decisions

### 1. Thin proxy, not a parallel implementation
The MCP server holds **no tool logic**. The agent exposes each tool's
core logic at a JSON `/tools/<name>` endpoint that bypasses the LLM
reasoning loop, and the MCP server forwards calls to it over HTTP.

Why: the agent already has the database connections, Qdrant client,
embedding pipeline, and summary-routing rules. Replicating any of that
in Rust would mean two implementations to keep in sync — and the first
divergence would silently change the answer Claude Code gets versus
what the chat UI gets. The thin-proxy boundary makes the agent the
single source of truth for tool behavior and the MCP server purely a
protocol adapter.

Cost: every MCP tool call adds one in-cluster HTTP hop. Mitigated by
co-locating the MCP server pod with the agent pod (`nodeSelector:
workload: ai`) so the hop stays on a single node.

### 2. Data-driven registry, not per-tool handlers
A `ToolDefinition` is a struct:

```rust
struct ToolDefinition {
    name: &'static str,
    description: &'static str,
    input_schema: serde_json::Value,
    agent_path: &'static str,
    method: HttpMethod,
    capability: Capability,   // Read | Write
}
```

The server holds a `Vec<ToolDefinition>`. **One** generic forwarder
reads the registry and dispatches every `tools/call` request. Adding a
new tool means appending one struct literal to `registry()` + adding
the matching agent endpoint. No new match arm, no new handler function.

Why: I knew there would be more tools (write tools in Phase 13+, email
ingestion later). With per-tool handlers the cost of each new tool is
two changes in two places that must agree (handler code + registration).
With a registry it's one entry in one place. And it forced the
discipline of articulating each tool as data — name, schema, agent
path, capability — which is what the MCP spec is shaped like anyway.

Cost: we explicitly do NOT use rmcp's `#[tool]` macros, which are
ergonomic but generate per-tool handler code. Hand-implementing
`ServerHandler::list_tools` and `::call_tool` is ~30 lines but
preserves the constraint.

### 3. Explicit `capability: Read | Write` field from v1
Even though Phase 12 is uniformly `Read`, every `ToolDefinition`
carries an explicit `capability` field. Phase 13's auth middleware
gates on it: only authenticated callers can invoke `Write` tools. The
field is the seam.

Why: introducing the field later means a backfill across every
registered tool plus a coordinated middleware rollout. Introducing it
up front means write tools — when they arrive — are a data-only
addition.

### 4. Streamable HTTP transport
The MCP spec's "streamable HTTP" transport (HTTP POST for requests,
optional SSE for responses) instead of stdio or pure-HTTP. Available
in rmcp 1.7 via `transport-streamable-http-server`.

Why:
- The server runs in-cluster and is reached over the network. stdio
  requires a subprocess per client, which doesn't model a remote
  shared service.
- Streamable HTTP is what Phase 13's Cloudflare Tunnel forwards — no
  transport rework when the tunnel lands.
- Claude Code (laptop) supports it natively via
  `claude mcp add --transport http`.

Cost: rmcp's streamable-http server has DNS-rebinding protection that
defaults to a loopback-only `Host:` allowlist. Anything fronted by an
ingress (Traefik with `mcp.local`, later the tunnel) must be added
explicitly. Surfaced as the `ALLOWED_HOSTS` env var.

---

## Alternatives considered

| Option                                                       | Rejected because                                                                                                                                                                                                                                  |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MCP server reimplements tool logic directly against Postgres/Qdrant | Two implementations to keep in sync; first divergence silently breaks parity between MCP and chat                                                                                                                                                  |
| `#[tool]` macros from rmcp                                   | Per-tool handler code; adding a tool becomes a code change in two places                                                                                                                                                                          |
| One ambiguous "fetch" or "query" tool with a free-form payload | Hides intent from the LLM; one specific tool per use case is what the LLM is good at routing over (same reasoning as [ADR 004](004-summary-based-rag.md)'s separate `find_documents` + `load_document`)                                            |
| stdio transport (subprocess per client)                      | Doesn't fit a remote shared service; would require running the MCP server on every laptop                                                                                                                                                         |
| Old HTTP-only transport (no SSE)                             | Deprecated in current MCP spec; Claude Code v2.1.177 prefers streamable HTTP                                                                                                                                                                       |
| Add the `capability` field in Phase 13 when needed           | Backfill across every existing tool definition + coordinated middleware rollout; cheaper to ship it from v1                                                                                                                                       |

---

## Consequences

- Every MCP tool call is an in-cluster HTTP hop on top of whatever the
  underlying tool does. Mitigated by pod co-location.
- The agent's `/tools/*` endpoints are now part of its API surface. They
  must stay stable across phases or the MCP server breaks. Mitigated
  because they're versioned with the agent and built/deployed atomically.
- All three retrieval paths (chat, MCP, future direct API consumers)
  flow through the same `_impl` helpers in the agent. A correctness fix
  there benefits all of them; a regression there breaks all of them.
- Adding a write tool in Phase 13+ is a data-only change in the
  registry plus the agent endpoint — but it also requires the auth
  middleware to be in place, since `Write` tools won't pass the
  capability gate without an authenticated caller.
- The thin-proxy decision also means the MCP server has no opinions
  about *which* data sources back a tool. A future tool that hits
  Twilio, n8n, or external APIs is just another `/tools/<name>`
  endpoint on the agent with its own registry entry — same shape.
