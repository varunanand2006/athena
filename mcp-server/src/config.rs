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
}

impl Config {
    pub fn from_env() -> Self {
        Self {
            agent_base_url: env::var("AGENT_BASE_URL")
                .unwrap_or_else(|_| "http://agent.athena.svc.cluster.local".to_string()),
            bind_address: env::var("BIND_ADDRESS")
                .unwrap_or_else(|_| "0.0.0.0:8080".to_string()),
        }
    }
}
