//! MCP protocol surface.
//!
//! Implements `ServerHandler` by hand (no `#[tool]` macros) so the
//! handler reads from the registry data instead of having one branch
//! per tool. The two interesting methods are:
//!
//!   - [`list_tools`]: maps every [`ToolDefinition`] in the registry
//!     to an MCP `Tool` and returns them in one page.
//!   - [`call_tool`]: looks up the requested tool by name, forwards
//!     its arguments to the agent via [`AgentClient`], and wraps the
//!     JSON response in a `CallToolResult::structured`.
//!
//! Adding a tool does not require touching this file. Append to the
//! registry and add the corresponding `/tools/<name>` endpoint on the
//! agent side.

use std::sync::Arc;

use rmcp::{
    model::{
        CallToolRequestParams, CallToolResult, Content, Implementation, ListToolsResult,
        PaginatedRequestParams, ProtocolVersion, ServerCapabilities, ServerInfo, Tool,
    },
    service::RequestContext,
    ErrorData, RoleServer, ServerHandler,
};
use serde_json::Value;
use tracing::{debug, error, warn};

use crate::agent_client::AgentClient;
use crate::registry::ToolDefinition;

/// MCP server impl for Athena. Holds the agent forwarder and a copy of
/// the tool registry. Cheap to clone — `AgentClient`'s reqwest pool is
/// internally `Arc`-shared, and `Vec<ToolDefinition>` is owned but
/// small (3 entries at v1).
#[derive(Clone)]
pub struct AthenaServer {
    agent: AgentClient,
    tools: Vec<ToolDefinition>,
}

impl AthenaServer {
    pub fn new(agent: AgentClient, tools: Vec<ToolDefinition>) -> Self {
        // Fail fast at startup if any registry entry's input_schema
        // isn't a JSON object — much friendlier than panicking inside
        // `list_tools` after a client has connected.
        for t in &tools {
            assert!(
                t.input_schema.is_object(),
                "tool {}'s input_schema must be a JSON object",
                t.name
            );
        }
        Self { agent, tools }
    }
}

impl ServerHandler for AthenaServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::from_build_env())
            .with_protocol_version(ProtocolVersion::V_2024_11_05)
            .with_instructions(
                "Athena MCP server. All tools proxy to the in-cluster Athena agent. \
                 For questions about the user's own documents (resume, projects, notes, \
                 class material), call find_documents(query) to identify the relevant \
                 document, then load_document(id_or_title) to read its full text — never \
                 answer substantive content questions from the summary alone. For LeetCode \
                 progress, breakdowns, or pattern analysis, call lookup_leetcode and reason \
                 over the returned `analysis` blobs (topic filtering is intentionally not \
                 done server-side).",
            )
    }

    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _ctx: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, ErrorData> {
        let tools = self
            .tools
            .iter()
            .map(|t| {
                // .as_object() is infallible — validated in new().
                let schema_map = t
                    .input_schema
                    .as_object()
                    .expect("input_schema validated at construction")
                    .clone();
                Tool::new(t.name, t.description, Arc::new(schema_map))
            })
            .collect();
        Ok(ListToolsResult {
            tools,
            next_cursor: None,
            meta: None,
        })
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _ctx: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        let Some(tool) = self.tools.iter().find(|t| t.name == request.name) else {
            warn!(name = %request.name, "tools/call for unknown tool");
            return Err(ErrorData::invalid_params(
                format!("Unknown tool: {}", request.name),
                None,
            ));
        };

        debug!(name = tool.name, "forwarding tools/call to agent");
        // `arguments` is Option<JsonObject>: clients are permitted to omit
        // it for tools with empty schemas (e.g. lookup_leetcode with no
        // filters). Treat absent as an empty object.
        let args = Value::Object(request.arguments.unwrap_or_default());
        match self.agent.call(tool, args).await {
            Ok(value) => Ok(CallToolResult::structured(value)),
            Err(e) => {
                // Tool-level failure, not protocol-level: return an
                // error CallToolResult so the MCP client / LLM can
                // surface the message rather than see a JSON-RPC fault.
                error!(name = tool.name, error = %e, "agent forwarder failed");
                Ok(CallToolResult::error(vec![Content::text(format!(
                    "Tool '{}' failed: {}",
                    tool.name, e
                ))]))
            }
        }
    }
}
