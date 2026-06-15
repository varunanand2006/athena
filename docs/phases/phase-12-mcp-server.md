# Phase 12 — Rust MCP Server

## Goal
Make Athena's three retrieval tools — `find_documents`, `load_document`,
`lookup_leetcode` — usable from Claude Code on the laptop, over the LAN,
through the Model Context Protocol. The MCP server is a **thin proxy**:
it speaks MCP to clients and plain HTTP to the agent. All real tool logic
stays in `agent/main.py` behind new `/tools/*` endpoints; the Rust binary
just translates between the two.

Two hard constraints carried through every step:

1. **Adding a future tool is a DATA change, not a code change.** The
   server holds a static `Vec<ToolDefinition>` and a single generic
   forwarder; appending a registry entry plus its matching agent
   endpoint adds a tool. No new handler function, no new match arm.
2. **Every `ToolDefinition` has an explicit `capability: Read | Write`
   field from v1.** Phase 12 ships uniformly `Read`, but the field is
   the seam Phase 13's auth middleware gates on without a refactor.

Phase 12 is LAN-only — no auth, no Cloudflare Tunnel. See
[ADR 005](../adr/005-mcp-thin-proxy.md) for the rationale.

## Phase gate
1. From the laptop, `curl http://mcp.local/healthz` returns `ok` through
   Traefik on `192.168.96.200`.
2. `claude mcp get athena` reports `Status: ✔ Connected` and lists three
   tools.
3. Inside a Claude Code session: *"Search my documents for resume and
   load the most relevant one."* fires `mcp__athena__find_documents` then
   `mcp__athena__load_document` and answers from real document text.
4. *"What's my LeetCode difficulty breakdown?"* fires
   `mcp__athena__lookup_leetcode` (no args) and returns the live count
   from Postgres.
5. Pod logs (`kubectl logs -n athena -l app=mcp-server`) show three
   `registered tool` lines on startup and `forwarding tools/call to
   agent` lines during the exercise.
6. Existing services unaffected: `/system` still green, `/chat` answers,
   `/leetcode` and `/internships` unchanged.

---

## What was built

### Agent (`agent/main.py`) — Step 1
Refactored the existing `find_documents`, `load_document`, and
`lookup_leetcode` `@tool` functions to call shared `_impl` helpers, then
exposed those helpers as JSON over three POST endpoints that bypass the
LLM reasoning loop:

| Endpoint                        | Body                                                  | Returns                                                              |
| ------------------------------- | ----------------------------------------------------- | -------------------------------------------------------------------- |
| `POST /tools/find_documents`    | `{"query": "..."}`                                    | top-3 `[{document_id, title, summary, score}]`                       |
| `POST /tools/load_document`     | `{"id_or_title": "..."}`                              | `{title, full_text}` or 404                                          |
| `POST /tools/lookup_leetcode`   | `{"difficulty"?, "since"?, "limit"?}` (all optional)  | `{breakdown, problems, filters}` raw JSON                            |

The chat path is unchanged — the `@tool` wrappers now thin-wrap the same
`_impl` helpers, so any structural fix to the underlying behavior
benefits both the LLM and the MCP caller. `lookup_leetcode` gained
optional filters at the SQL layer (case-insensitive `difficulty`,
`since` date); topic/pattern filtering is intentionally NOT server-side
because the MCP client is expected to reason over the returned
`analysis` blobs.

### Rust MCP server (`mcp-server/`) — Steps 2-4
Four files, each with one job:

- **`Cargo.toml`** — `rmcp 1.7` with the `server` and
  `transport-streamable-http-server` features, plus `axum`, `tokio`,
  `reqwest` (rustls), `serde`, `thiserror`, `tracing`. Comment block
  explains why we use `rmcp` + a hand-written `ServerHandler` instead of
  the `#[tool]` macros: the macros are per-tool handler code, which
  would violate constraint #1.
- **`src/config.rs`** — `Config::from_env()` reads `AGENT_BASE_URL`,
  `BIND_ADDRESS`, and `ALLOWED_HOSTS` (comma-separated).
- **`src/registry.rs`** — `ToolDefinition { name, description,
  input_schema, agent_path, method, capability }`, `Capability { Read |
  Write }`, `HttpMethod { Get | Post }`, and a `registry()` function
  returning the three v1 tools with their JSON Schemas. Module doc block
  documents the extension pattern.
- **`src/agent_client.rs`** — `AgentClient::call(&ToolDefinition,
  Value) -> Result<Value, AgentClientError>`. Single entry point, zero
  per-tool branching. Errors typed via `thiserror`.
- **`src/server.rs`** — `AthenaServer` impl `ServerHandler` (manually,
  not via macros):
  - `get_info` — enables `tools` capability, sets `ProtocolVersion::V_2024_11_05`,
    instructions guide the LLM to use `find_documents → load_document`
    for content questions.
  - `list_tools` — maps every registry entry to `rmcp::model::Tool`.
  - `call_tool` — looks up by name, forwards to `AgentClient`, wraps
    success in `CallToolResult::structured(json)` (which provides both
    structured content AND a text fallback for clients that don't
    parse structured content). Forwarder errors become
    `CallToolResult::error(text)` so the LLM sees the message instead
    of a JSON-RPC fault.
- **`src/main.rs`** — wires `StreamableHttpService::new(...)` at `/mcp`
  inside its own `Router`, leaving a single `.layer(...)` slot for the
  Phase 13 auth middleware to plug into. `/healthz` is mounted at the
  top level for k8s probes. Graceful shutdown via
  `CancellationToken` + `ctrl_c`.

### Cluster deploy (`cluster/mcp-server/`) — Step 5

- **`mcp-server/Dockerfile`** — multi-stage. `rust:1-slim-bookworm`
  builder (some current transitive crates need edition 2024, which
  requires Rust 1.85+); dep-cache primed via dummy `main.rs`, then real
  source compiled. Runtime stage is `debian:bookworm-slim` with only
  `ca-certificates`, runs as a non-root uid 10001.
- **`deployment.yaml`** — `nodeSelector: workload: ai` so the pod is
  co-located with the agent on `xdev-sr` (every tool call becomes one
  in-cluster HTTP hop and we keep it on the same node). `imagePullPolicy:
  Never` with `athena-mcp-server:phase12`. Env vars: `AGENT_BASE_URL`,
  `BIND_ADDRESS`, `ALLOWED_HOSTS`. `/healthz` probes.
- **`service.yaml`** — ClusterIP, port 80 → containerPort 8080.
- **`ingress.yaml`** — Traefik, `host: mcp.local`. Laptop hosts file
  maps `mcp.local` → `192.168.96.200`.

### Claude Code integration — Step 6
Registered via `claude mcp add --transport http athena --scope user
http://mcp.local/mcp`. Resulting `~/.claude.json` entry:

```json
{
  "mcpServers": {
    "athena": {
      "type": "http",
      "url": "http://mcp.local/mcp"
    }
  }
}
```

`mcp-server/README.md` documents the full registration flow, the two
gate exercises, and a troubleshooting table.

---

## Build process

Standard CLAUDE.md flow per the
[image-tag workflow memory](../../CLAUDE.md): build on xdev-sr (where
docker lives), tag as `:phase12`, save without gzip, import into
containerd, then `kubectl apply` from vlinux1.

```bash
# Windows → push
git add agent/main.py mcp-server/ cluster/mcp-server/ CLAUDE.md docs/
git commit -m "feat: phase 12 — Rust MCP server"
git push

# xdev-sr — build agent and MCP server
ssh ubuntu@192.168.96.201
cd ~/athena && git pull
sudo docker build -t athena-agent:phase12      ./agent
sudo docker build -t athena-mcp-server:phase12 ./mcp-server
sudo docker save -o /tmp/athena-agent.tar      athena-agent:phase12
sudo docker save -o /tmp/athena-mcp-server.tar athena-mcp-server:phase12
sudo chmod 644 /tmp/athena-*.tar
sudo k3s ctr images import /tmp/athena-agent.tar
sudo k3s ctr images import /tmp/athena-mcp-server.tar

# vlinux1 — apply manifests
kubectl apply -f cluster/agent/deployment.yaml
kubectl apply -f cluster/mcp-server/
kubectl rollout status deployment/agent      -n athena
kubectl rollout status deployment/mcp-server -n athena

# Laptop (Windows admin PowerShell, once)
Add-Content -Path "$env:windir\System32\drivers\etc\hosts" -Value "192.168.96.200 mcp.local"

# Laptop (normal terminal)
claude mcp add --transport http athena --scope user http://mcp.local/mcp
claude mcp get athena   # expect: Connected, 3 tools
```

---

## Issues encountered

### `:latest` vs `:phaseN` tag drift
The deployed agent was running `athena-agent:phase11` while we kept
rebuilding `:latest`. The committed deployment.yaml said `:latest`,
which masked the divergence. Fix: bump the Deployment manifest to
`:phase12` and ship that with the code; declarative reconcile (`kubectl
apply`) now keeps cluster and repo in sync. Recorded in
[project-image-tag-workflow memory](../../memory/...) for future
phases.

### Rust edition 2024 in transitive deps
`cpufeatures 0.3.0` (pulled by reqwest's rustls path) requires edition
2024, which needs Rust 1.85+. Builder image was pinned at `:1.82`;
bumped to `:1` (tracks latest stable) since rmcp/axum/reqwest churn
faster than any pinned older toolchain can serve.

### `ListToolsResult.meta` + `CallToolRequestParams.arguments` is `Option`
Two minor API-shape mismatches against my source-of-truth doc reads:
`ListToolsResult` carries a required `meta: Option<Meta>` field, and
`arguments` is `Option<JsonObject>` (clients are permitted to omit it
for tools whose schemas have no required fields — which is exactly our
`lookup_leetcode` case). Both fixes also legitimately improve behavior.

### rmcp DNS-rebinding guard rejected `Host: mcp.local`
The biggest gotcha. rmcp 1.7's `StreamableHttpService` defaults to an
allowlist of loopback-only hosts and returns 403 for anything else — a
security feature against DNS-rebinding attacks against locally-running
servers. Every Traefik-routed request was hitting the 403 with
`"Host header is not allowed"`. Fixed via
`StreamableHttpServerConfig::with_allowed_hosts(cfg.allowed_hosts)`,
populated from the `ALLOWED_HOSTS` env var (default includes
`mcp.local`). The env var slot in `cluster/mcp-server/deployment.yaml`
is where Phase 13's Cloudflare Tunnel hostname will be added without a
recompile.

### Claude Code "Needs authentication" sticky badge
Claude Code's MCP client (v2.1.177) marks any HTTP MCP server that ever
returns 401/403 during registration as needing OAuth — and the verdict
sticks across restarts. After fixing the 403, `claude mcp remove athena
-s user` + `claude mcp add ...` were required for the client to re-probe
and see the now-clean 200s. Documented in the README troubleshooting
table.

---

## Next phase

**Phase 13 — auth + Cloudflare Tunnel.** The seams already exist:

- `mcp-server/src/main.rs` keeps `/mcp` in its own axum `Router` so a
  single `.layer(axum::middleware::from_fn(auth_middleware))` line
  covers every MCP method uniformly.
- `ToolDefinition::capability` already distinguishes `Read | Write`; the
  middleware gates on it.
- `ALLOWED_HOSTS` is env-configurable so the tunnel hostname can be
  added there.
- The streamable HTTP transport is what the tunnel will forward, so no
  transport rework.

Once Phase 13 lands, write tools become a data-only addition (`Write`
capability), the LAN-only constraint is lifted, and the OAuth handshake
Claude Code wants gets a real endpoint to negotiate with.
