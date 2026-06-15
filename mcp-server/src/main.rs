//! Athena MCP server.
//!
//! This binary is a thin proxy: it speaks MCP (over the streamable
//! HTTP transport) to clients and plain HTTP to the agent service. It
//! holds no business logic. The agent owns the real tool
//! implementations behind /tools/* endpoints; this server translates
//! MCP `tools/list` / `tools/call` into agent HTTP calls.
//!
//! ## Boundary: LAN-only in Phase 12
//!
//! There is NO auth in this phase. The server MUST NOT be exposed
//! beyond the LAN until Phase 13 adds the auth middleware and the
//! Cloudflare Tunnel. The middleware seam is the `.layer(...)` slot on
//! the `/mcp` route group below — Phase 13 plugs in there and gates on
//! the registry's read/write `Capability` field. The same gate is what
//! lets us safely introduce write tools later.
//!
//! ## Architectural layers
//!
//!   - `registry`     — static `ToolDefinition` list (data, not code).
//!   - `agent_client` — generic forwarder; one `call(&ToolDef, args)`
//!     entry point, no per-tool branching.
//!   - `server`       — MCP `ServerHandler` impl; reads the registry,
//!     dispatches to the forwarder. Adding a tool does not touch it.

use std::net::SocketAddr;
use std::sync::Arc;

use anyhow::Result;
use axum::{middleware, routing::get, Router};
use rmcp::transport::streamable_http_server::{
    session::local::LocalSessionManager, StreamableHttpServerConfig, StreamableHttpService,
};
use tokio_util::sync::CancellationToken;
use tracing::{info, warn};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

mod agent_client;
mod auth;
mod config;
mod registry;
mod server;

use agent_client::AgentClient;
use config::Config;
use server::AthenaServer;

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
        allowed_hosts = ?cfg.allowed_hosts,
        auth_enabled = cfg.auth_token.is_some(),
        "starting athena-mcp-server"
    );
    if cfg.auth_token.is_none() {
        warn!(
            "MCP_AUTH_TOKEN is not set — bearer-token auth is unconfigured. \
             The middleware will reject every /mcp request (fail-closed)."
        );
    }

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

    let agent = AgentClient::new(cfg.agent_base_url.clone());
    let server = AthenaServer::new(agent, tools);

    let ct = CancellationToken::new();
    let mcp_service = StreamableHttpService::new(
        {
            // The factory is called once per session by the SDK.
            // `AthenaServer` is `Clone` and cheap to clone (Arc inside).
            let server = server.clone();
            move || Ok(server.clone())
        },
        LocalSessionManager::default().into(),
        StreamableHttpServerConfig::default()
            .with_cancellation_token(ct.child_token())
            // Default allowlist is loopback-only; the ingress host (and any
            // tunnel host added in Phase 13) must be added explicitly or
            // rmcp returns 403 with "Host header is not allowed".
            .with_allowed_hosts(cfg.allowed_hosts.clone()),
    );

    // Phase 13 auth: every request through /mcp is gated by a bearer-token
    // check before it reaches rmcp. /healthz stays at the top level outside
    // this Router so k8s probes are unaffected by auth state.
    let auth_state = Arc::new(auth::AuthState::new(cfg.auth_token.clone()));
    let mcp_routes = Router::new()
        .nest_service("/mcp", mcp_service)
        .layer(middleware::from_fn_with_state(
            auth_state,
            auth::auth_middleware,
        ));

    let app = Router::new()
        .route("/healthz", get(healthz))
        .merge(mcp_routes);

    let addr: SocketAddr = cfg.bind_address.parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    info!(%addr, "listening");

    axum::serve(listener, app)
        .with_graceful_shutdown(async move {
            let _ = tokio::signal::ctrl_c().await;
            ct.cancel();
        })
        .await?;
    Ok(())
}

async fn healthz() -> &'static str {
    "ok"
}
