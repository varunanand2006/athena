# ADR 006 — MCP Authorization Granularity (Capability Gating)

**Date:** 2026-06-15
**Status:** Accepted

---

## Context

[ADR 005](005-mcp-thin-proxy.md) introduced an explicit
`capability: Read | Write` field on every `ToolDefinition` and stated
that "Phase 13's auth middleware gates on it: only authenticated
callers can invoke `Write` tools." `CLAUDE.md` repeats this, describing
the Phase 13 seam as gating "uniformly on the capability field."

Phase 13 landed the auth middleware (`mcp-server/src/auth.rs`) and the
Cloudflare Tunnel. While verifying the MCP server end-to-end, we
re-read the wiring and found the capability-gating claim is **not yet
true, and cannot be true at the layer where the gate currently lives**:

- `auth_middleware` is attached with `middleware::from_fn_with_state`
  onto the whole `/mcp` route group (`main.rs`). It is a single
  bearer-token check: valid token → every tool allowed; missing/invalid
  token → everything rejected (fail-closed, always `401`, constant-time
  compare). All of that is correct and intentional.
- The middleware runs at the **HTTP layer**. But *which* tool — and
  therefore its `capability` — is named inside the **MCP JSON-RPC POST
  body** (`tools/call` → `params.name`), not in the HTTP method or path.
  A `from_fn` HTTP middleware does not see the decoded tool name without
  reading and re-buffering the request body and parsing JSON-RPC itself.

So today the gate is **all-or-nothing**: authentication, not
capability-aware authorization. This is harmless while every tool is
`Read` (the v1 state). It becomes a real gap the moment a `Write` tool
is added, because such a tool would be reachable by anyone holding the
single bearer token — there is no separate gate for mutation.

This ADR records the actual state and decides where capability gating
will live when write tools arrive, so the gap is not rediscovered as a
surprise mid-implementation.

---

## Decisions

### 1. Keep bearer-token-at-the-router as the authentication layer

The existing `auth_middleware` stays exactly as is for
*authentication* (is this caller allowed to talk to the MCP server at
all?). It correctly fail-closes, fronts the tunnel, and is uniform
across every MCP method. No change.

### 2. Capability gating belongs in `call_tool`, not the middleware

When write tools are introduced, the read/write *authorization* check
goes inside `ServerHandler::call_tool` in `server.rs`, **not** in the
HTTP middleware.

Why `call_tool`:

- It already holds the matched `&ToolDefinition` (it looks the tool up
  by name to forward it), so `tool.capability` is in hand with zero
  extra parsing.
- It is the one place that already knows the decoded MCP request. The
  middleware would have to re-implement JSON-RPC body parsing to learn
  the same thing — duplicating work and adding a body-buffering cost to
  every request.
- It keeps the thin-proxy shape: the gate is one `if` over registry
  data (`match tool.capability { Write => ..., Read => ... }`), not new
  per-tool code. Consistent with ADR 005's "adding a tool is a data
  change."

The middleware remains the coarse gate (authenticated or not); the
fine gate (is this caller allowed to *mutate*?) sits where the
capability is actually known.

### 3. The write-authz policy itself is deferred, not designed here

This ADR does **not** decide *how* writes are authorized — only *where*
the check lives. The actual policy (a second token for writes;
LAN-origin-only writes while the tunnel stays read-only; per-token
capability scopes; etc.) is deferred to the phase that introduces the
first write tool. Whatever is chosen, it is read inside `call_tool`
against `tool.capability`, so the decision surface is small and local.

### 4. Correct the over-claim in ADR 005 and CLAUDE.md

ADR 005 and CLAUDE.md describe capability gating as already in force.
That is aspirational. This ADR supersedes those statements: as of Phase
13 the `capability` field is **carried but not yet enforced**. The
field still earns its place — it makes enforcement a data-driven `if`
when the time comes (the original reason for shipping it from v1), and
it documents intent on every tool. It simply is not wired to a gate
yet.

---

## Alternatives considered

| Option | Rejected because |
| ------ | ---------------- |
| Parse the JSON-RPC body in `auth_middleware` to extract the tool name and gate on capability there | Re-buffers and re-parses every request body at the HTTP layer, duplicating decoding the MCP handler already does; couples the middleware to MCP wire format; the matched `ToolDefinition` is already available downstream in `call_tool` |
| Split read tools and write tools onto separate URL paths (e.g. `/mcp` vs `/mcp-write`) so the existing path-level middleware can gate them | Breaks the single MCP endpoint clients register; the MCP transport multiplexes all `tools/call` over one connection, so a second path doesn't map onto how clients speak the protocol |
| Leave it fully uniform (single token, no capability distinction) even after write tools land | A leaked or shared read token would also grant mutation; the whole point of the `capability` field was to let the read/write boundary be enforced |
| Design the full write-authz policy now | No write tool exists yet; designing the policy before its first concrete use risks guessing wrong. Deciding only *where* the check lives is enough to de-risk the future change |

---

## Consequences

- **No behavior change today.** Every tool is `Read`; the single bearer
  token is both necessary and sufficient. The server is correctly
  protected for its current surface.
- **The first write tool is no longer a data-only change.** Contrary to
  ADR 005's consequence list, adding a `Write` tool requires (a) the
  registry entry + agent endpoint *and* (b) wiring the capability check
  in `call_tool` plus choosing a write-authz policy. The first write
  tool pays a one-time code cost; every subsequent write tool is
  data-only again.
- **The enforcement point is pinned.** Future work has a single, small
  place to add the gate (`call_tool`), with `tool.capability` already in
  scope — no architectural rediscovery needed.
- **ADR 005 / CLAUDE.md are corrected by reference.** Readers who hit
  the "gates on the capability field" claim should treat this ADR as the
  current truth: carried, not yet enforced.

---

## Related

- [ADR 005 — MCP Server as Thin Proxy](005-mcp-thin-proxy.md) — introduced
  the `capability` field and the (aspirational) gating claim this ADR
  corrects.
- `mcp-server/src/auth.rs` — the authentication middleware (unchanged by
  this ADR).
- `mcp-server/src/server.rs` — `call_tool`, the chosen home for future
  capability gating.
