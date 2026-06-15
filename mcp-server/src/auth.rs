//! Bearer-token auth middleware for the `/mcp` route group.
//!
//! Phase 13 makes the MCP server reachable from the public internet via a
//! Cloudflare Tunnel. This middleware is the only thing standing between the
//! tunnel and the read tools, so its rules are deliberately boring:
//!
//!   - Token comes from the `MCP_AUTH_TOKEN` env var, loaded once at startup
//!     into `Config::auth_token`. The middleware never reads env at runtime.
//!   - **Fail closed.** If the token is unset (`None`), every request is
//!     rejected. Misconfiguration must never silently expose tools.
//!   - **Always 401**, never 403. Claude Code's MCP client interprets a 403
//!     during registration as "OAuth required" and stickily marks the server
//!     as auth-required across restarts (lesson from Phase 12). 401 with a
//!     `WWW-Authenticate: Bearer` header is the RFC 6750 shape and what the
//!     client expects for header-based auth.
//!   - **Constant-time compare** via `subtle::ConstantTimeEq` — no length
//!     short-circuit, no early-return on first byte mismatch.
//!
//! `/healthz` is intentionally NOT covered: it lives outside the `mcp_routes`
//! Router in `main.rs` so k8s probes keep working regardless of auth state.

use std::sync::Arc;

use axum::{
    body::Body,
    extract::State,
    http::{header, HeaderValue, Request, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
};
use subtle::ConstantTimeEq;
use tracing::warn;

/// Shared state for the middleware. Holds the expected token (or `None` if
/// auth is unconfigured — see fail-closed behavior above).
pub struct AuthState {
    expected: Option<String>,
}

impl AuthState {
    pub fn new(expected: Option<String>) -> Self {
        Self { expected }
    }
}

pub async fn auth_middleware(
    State(state): State<Arc<AuthState>>,
    req: Request<Body>,
    next: Next,
) -> Response {
    let Some(expected) = state.expected.as_deref() else {
        // Token unconfigured. Warn (per request — cheap, and noisy logs make
        // the misconfiguration impossible to miss) and reject.
        warn!("MCP_AUTH_TOKEN is not set — rejecting request (fail-closed)");
        return unauthorized();
    };

    let provided = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "));

    let Some(provided) = provided else {
        return unauthorized();
    };

    if !bool::from(provided.as_bytes().ct_eq(expected.as_bytes())) {
        return unauthorized();
    }

    next.run(req).await
}

fn unauthorized() -> Response {
    let mut resp = (
        StatusCode::UNAUTHORIZED,
        [(header::CONTENT_TYPE, "application/json")],
        r#"{"error":"unauthorized"}"#,
    )
        .into_response();
    resp.headers_mut().insert(
        header::WWW_AUTHENTICATE,
        HeaderValue::from_static("Bearer"),
    );
    resp
}
