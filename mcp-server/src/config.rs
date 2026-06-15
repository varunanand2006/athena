//! Server configuration, read once at startup from env vars.

use std::env;

#[derive(Debug, Clone)]
pub struct Config {
    /// Base URL of the in-cluster agent service. The MCP server proxies tool
    /// calls to /tools/* under this URL.
    pub agent_base_url: String,

    /// Address the HTTP server binds to. `0.0.0.0` in-cluster so the Service
    /// can reach it from other pods.
    pub bind_address: String,

    /// Host values the MCP streamable-http transport will accept. rmcp's
    /// default is loopback-only (DNS-rebinding protection); anything served
    /// behind a Traefik ingress or a Cloudflare Tunnel must be added here or
    /// every request 403s with "Host header is not allowed". Phase 13's
    /// tunnel hostname will join this list without a recompile.
    pub allowed_hosts: Vec<String>,

    /// Shared bearer token enforced by the Phase 13 auth middleware. `None`
    /// means unconfigured: the middleware fails closed and rejects every
    /// request with 401. There is intentionally no default — a missing token
    /// must never silently mean "open to the internet".
    pub auth_token: Option<String>,
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            agent_base_url: env::var("AGENT_BASE_URL")
                .unwrap_or_else(|_| "http://agent.athena.svc.cluster.local".to_string()),
            bind_address: env::var("BIND_ADDRESS")
                .unwrap_or_else(|_| "0.0.0.0:8080".to_string()),
            allowed_hosts: env::var("ALLOWED_HOSTS")
                .unwrap_or_else(|_| {
                    "mcp.local,mcp.local:80,localhost,127.0.0.1,0.0.0.0".to_string()
                })
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect(),
            auth_token: env::var("MCP_AUTH_TOKEN")
                .ok()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty()),
        }
    }
}
