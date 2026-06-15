//! Tool registry — the extensibility seam for the MCP server.
//!
//! # Adding a tool
//!
//! Adding a new tool is a DATA change, not new handler code:
//!
//!   1. Add a `/tools/<name>` endpoint to the agent (`agent/main.py`).
//!      The endpoint must return clean JSON and accept its arguments in
//!      the JSON request body.
//!   2. Append a single `ToolDefinition` to [`registry()`] below with
//!      its name, description, input JSON Schema, `agent_path`,
//!      `method`, and `capability`.
//!
//! No new match arm, no new handler function. The generic forwarder in
//! [`crate::agent_client`] reads the registry and routes the call.
//!
//! # Capability — the Phase 13 auth seam
//!
//! Every `ToolDefinition` carries an explicit [`Capability`] of `Read`
//! or `Write`. Phase 12 (v1) is uniformly `Read`, but the field MUST
//! exist from the first commit so Phase 13's auth middleware can gate
//! on the read/write boundary without a refactor. The Cloudflare
//! Tunnel (also Phase 13) only exposes what passes that gate.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Capability {
    Read,
    Write,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum HttpMethod {
    Get,
    Post,
}

/// Definition of one tool exposed by the MCP server.
///
/// The fields map directly onto the MCP `tool` schema (`name`,
/// `description`, `inputSchema`) plus the proxying metadata
/// (`agent_path`, `method`, `capability`) the forwarder needs to route
/// the call to the agent.
#[derive(Debug, Clone)]
pub struct ToolDefinition {
    pub name: &'static str,
    pub description: &'static str,
    /// JSON Schema (draft-7 style) describing the tool's input object.
    pub input_schema: Value,
    /// Path on the agent service that implements this tool.
    pub agent_path: &'static str,
    pub method: HttpMethod,
    pub capability: Capability,
}

/// The static set of tools the MCP server exposes.
///
/// Built as a function rather than `const` because `serde_json::Value`
/// cannot be a const. The caller is expected to build this once at
/// startup and hold it for the process lifetime.
pub fn registry() -> Vec<ToolDefinition> {
    vec![
        ToolDefinition {
            name: "find_documents",
            description:
                "Find which documents in the user's library are relevant to a query by \
                 searching their summaries. Returns up to 3 matches with document_id, \
                 title, summary, and similarity score. This is the *routing* step — it \
                 tells you which document to read, not the answer itself. After calling \
                 this, call load_document with one of the returned ids (or titles) to \
                 read the full text.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language description of what to look for."
                    }
                },
                "required": ["query"],
                "additionalProperties": false
            }),
            agent_path: "/tools/find_documents",
            method: HttpMethod::Post,
            capability: Capability::Read,
        },
        ToolDefinition {
            name: "load_document",
            description:
                "Load a document's full text from the catalog, given its id (UUID) OR \
                 a substring of its title/filename. Returns {title, full_text}. Use \
                 AFTER find_documents to read the content needed to answer a question — \
                 never answer substantive questions from the summary alone.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "id_or_title": {
                        "type": "string",
                        "description": "Document UUID, or a substring of its title or filename."
                    }
                },
                "required": ["id_or_title"],
                "additionalProperties": false
            }),
            agent_path: "/tools/load_document",
            method: HttpMethod::Post,
            capability: Capability::Read,
        },
        ToolDefinition {
            name: "lookup_leetcode",
            description:
                "Look up the user's LeetCode activity from Postgres. Returns raw \
                 structured JSON: solved problems (with their stored per-problem \
                 analyses) plus an overall easy/medium/hard breakdown. All filters \
                 are optional; with no arguments returns the 15 most recently solved \
                 problems plus the breakdown. Topic/pattern filtering is intentionally \
                 NOT done server-side — reason over the returned `analysis` blobs.",
            input_schema: json!({
                "type": "object",
                "properties": {
                    "difficulty": {
                        "type": "string",
                        "enum": ["easy", "medium", "hard"],
                        "description": "Optional case-insensitive difficulty filter."
                    },
                    "since": {
                        "type": "string",
                        "description": "Optional ISO date (YYYY-MM-DD); only return problems solved on/after this date."
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "description": "Optional cap on number of problems returned (default 15)."
                    }
                },
                "additionalProperties": false
            }),
            agent_path: "/tools/lookup_leetcode",
            method: HttpMethod::Post,
            capability: Capability::Read,
        },
    ]
}
