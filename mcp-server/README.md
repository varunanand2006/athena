# Athena MCP Server

Rust HTTP/SSE MCP server that proxies tool calls to the in-cluster Athena
agent. **LAN-only in Phase 12** — no auth, no Cloudflare Tunnel. Do not
expose this server publicly until Phase 13 adds the auth middleware.

- Transport: streamable HTTP (`POST /mcp` + SSE)
- Source: `src/` (this directory) — entry point `main.rs`
- Cluster manifests: `../cluster/mcp-server/`
- LAN URL: `http://mcp.local/mcp`

## Tools exposed (v1)

All `capability: read`. See `src/registry.rs` for the canonical
definitions.

| Tool              | Agent endpoint                  | Input                                            |
| ----------------- | ------------------------------- | ------------------------------------------------ |
| `find_documents`  | `POST /tools/find_documents`    | `{query: string}`                                |
| `load_document`   | `POST /tools/load_document`     | `{id_or_title: string}`                          |
| `lookup_leetcode` | `POST /tools/lookup_leetcode`   | `{difficulty?, since?, limit?}` (all optional)   |

## One-time laptop setup

`mcp.local` resolves to the k3s control plane (Traefik) at
`192.168.96.200`. From an **administrator** PowerShell:

```powershell
Add-Content -Path "$env:windir\System32\drivers\etc\hosts" -Value "192.168.96.200 mcp.local"
curl http://mcp.local/healthz   # expect: ok
```

## Register with Claude Code

User scope so the server is available from any project on the laptop:

```bash
claude mcp add --transport http athena --scope user http://mcp.local/mcp
```

This writes the following entry into `~/.claude.json`:

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

The MCP spec calls this transport `streamable-http`; Claude Code accepts
either `"type": "http"` or `"type": "streamable-http"` if you edit the
JSON directly.

## Verify

```bash
# Should list `athena` along with any other configured servers
claude mcp list

# Should report status: connected, list 3 tools
claude mcp get athena
```

Inside a Claude Code session you should see `mcp__athena__find_documents`,
`mcp__athena__load_document`, and `mcp__athena__lookup_leetcode` in the
tool inventory.

## Exercise (Phase 12 gate)

End-to-end the LAN path with Claude Code on the laptop:

- **Documents**: "Search my documents for resume and load the most relevant
  one." → expect `find_documents` then `load_document` calls, ending in an
  answer drawn from the full text.
- **LeetCode**: "What's my LeetCode difficulty breakdown, and show me the
  stored analyses for my recent hard problems." → expect a single
  `lookup_leetcode` call with `{"difficulty":"hard"}`, then a reply that
  reasons over the returned `analysis` blobs.

## Troubleshooting

| Symptom                                                         | Likely cause                                     | Fix                                                                                              |
| --------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `curl http://mcp.local/healthz` hangs or `Could not resolve`    | hosts file entry missing                         | Re-run the `Add-Content` step above                                                              |
| `claude mcp get athena` reports `failed to connect`             | server pod down, or wrong path (`/mcp` required) | `kubectl get pods -n athena -l app=mcp-server` and check the URL ends in `/mcp`                  |
| `claude mcp list` shows server but no tools                     | initialize handshake failed                      | Check pod logs for the 3 `registered tool` lines; if absent, registry construction panicked      |
| Tool call returns `Tool '<name>' failed: ...`                   | Agent forwarder reached the agent but got a non-2xx | Hit the matching agent `/tools/<name>` endpoint directly with `curl` to isolate                |
| Tool call hangs                                                 | Agent ingestion / Postgres / Ollama issue        | `kubectl logs -n athena -l app=mcp-server` shows the outbound URL; `kubectl logs -l app=agent` next |

Pod logs:

```bash
POD=$(kubectl get pods -n athena -l app=mcp-server -o jsonpath='{.items[0].metadata.name}')
kubectl logs -n athena $POD --tail=50
```

## Extending the server (adding a tool)

Single source of truth: `src/registry.rs`. Steps:

1. Add a matching endpoint on the agent (`agent/main.py`) under `/tools/`.
2. Append one `ToolDefinition` to `registry()` with its name, description,
   `input_schema` (JSON Schema draft-7), `agent_path`, `method`, and
   `capability` (`Read` or `Write`).
3. Rebuild and redeploy per `cluster/mcp-server/`.

No new handler function. The generic forwarder in `src/agent_client.rs`
routes every tool through the same code path. The `capability` field is
the Phase 13 auth seam — write tools will require an authenticated
caller.
