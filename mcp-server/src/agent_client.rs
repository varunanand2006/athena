//! Generic agent forwarder.
//!
//! Takes a [`ToolDefinition`] from the registry and a JSON arguments
//! object from the MCP `tools/call` request, sends an HTTP request to
//! the mapped agent endpoint, and relays the JSON response. There is
//! intentionally no per-tool branching here — adding a tool stays a
//! data-only change in [`crate::registry`].

use reqwest::{Client, Method};
use serde_json::Value;
use thiserror::Error;

use crate::registry::{HttpMethod, ToolDefinition};

#[derive(Debug, Error)]
pub enum AgentClientError {
    #[error("agent request failed: {0}")]
    Request(#[from] reqwest::Error),

    #[error("agent returned status {status}: {body}")]
    Status { status: u16, body: String },

    #[error("agent response was not valid JSON: {0}")]
    Json(String),
}

/// Thin HTTP forwarder. Holds a reqwest `Client` (connection pool) and
/// the agent's base URL; cloned cheaply (`Client` is `Arc` inside).
#[derive(Debug, Clone)]
pub struct AgentClient {
    base_url: String,
    http: Client,
}

impl AgentClient {
    pub fn new(base_url: String) -> Self {
        Self {
            base_url,
            http: Client::new(),
        }
    }

    /// Forward an MCP `tools/call` invocation to the agent.
    ///
    /// `args` is the JSON object the MCP client supplied — it is sent
    /// verbatim as the request body so the agent endpoint sees exactly
    /// what its Pydantic model expects. The response body is returned
    /// as a `serde_json::Value` for the MCP handler to wrap in a
    /// `CallToolResult`.
    pub async fn call(
        &self,
        tool: &ToolDefinition,
        args: Value,
    ) -> Result<Value, AgentClientError> {
        let url = format!("{}{}", self.base_url, tool.agent_path);
        let method = match tool.method {
            HttpMethod::Get => Method::GET,
            HttpMethod::Post => Method::POST,
        };

        let mut req = self.http.request(method, &url);
        // Send the JSON body unconditionally — even GETs in the registry
        // would carry their args this way if we ever added one. Today
        // all entries are POST.
        req = req.json(&args);

        let resp = req.send().await?;
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(AgentClientError::Status {
                status: status.as_u16(),
                body,
            });
        }
        let value = resp
            .json::<Value>()
            .await
            .map_err(|e| AgentClientError::Json(e.to_string()))?;
        Ok(value)
    }
}
