//! Athena MCP server — Phase 12 scaffold.
//!
//! This binary is a thin proxy: it speaks MCP (over streamable HTTP) to
//! clients and plain HTTP to the agent service. It holds no business logic.
//! The agent owns the real tool implementations behind /tools/* endpoints
//! (added in Step 1); this server translates MCP tool calls into agent HTTP
//! calls.
//!
//! At this scaffold stage the server only boots, logs its configuration, and
//! serves /healthz. The tool registry (Step 3) and MCP protocol surface
//! (Step 4) land in subsequent commits — wiring them in must be additive,
//! not require restructuring this file.
//!
//! LAN-only, no auth in Phase 12 — see CLAUDE.md. Phase 13 adds the auth
//! middleware layer and the Cloudflare Tunnel; both gate on the read/write
//! capability field carried by every ToolDefinition.

use std::net::SocketAddr;

use anyhow::Result;
use axum::{routing::get, Router};
use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

mod agent_client;
mod config;
mod registry;

use agent_client::AgentClient;
use config::Config;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .with(tracing_subscriber::fmt::layer())
        .init();

    let cfg = Config::from_env();
    info!(
        bind_address = %cfg.bind_address,
        agent_base_url = %cfg.agent_base_url,
        "starting athena-mcp-server"
    );

    let tools = registry::registry();
    for t in &tools {
        info!(
            tool = t.name,
            capability = ?t.capability,
            method = ?t.method,
            agent_path = t.agent_path,
            "registered tool"
        );
    }

    // Constructed here so its connection pool is reused across requests.
    // Step 4 hands this + the registry to the rmcp ServerHandler.
    let _agent = AgentClient::new(cfg.agent_base_url.clone());

    // Router is intentionally tiny. Step 4 mounts the rmcp
    // StreamableHttpService at /mcp behind a middleware seam so Phase 13
    // auth can slot in without restructuring.
    let app = Router::new().route("/healthz", get(healthz));

    let addr: SocketAddr = cfg.bind_address.parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    info!(%addr, "listening");
    axum::serve(listener, app).await?;
    Ok(())
}

async fn healthz() -> &'static str {
    "ok"
}
