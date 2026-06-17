"""Thin read-only Gmail client (Phase 19).

Gmail is a *lookup source*, not a memory source — the agent reaches for it to
answer "did X reply?", "what did the recruiter say?", "find the email about Y",
the same way it reaches for `load_document` / `lookup_leetcode`. It does NOT
auto-feed the memory vault or the temporal `events` system (that's a separate,
later, filtering-policy phase).

HARD SECURITY BOUNDARY: the only scope referenced anywhere in this module is
`gmail.readonly`. The minted credential is physically incapable of sending,
drafting, deleting, modifying, or labeling — there are no such calls here, by
construction. Keep it that way: this module is read + search ONLY.

Credentials come from three env vars (a k8s secret in the `athena` namespace):
GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN. The long-lived
refresh token is what's stored; the Google client library transparently mints
short-lived access tokens from it on each call.
"""

import base64
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# The ONLY scope this module knows about. Read-only — no mutation capability.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Token endpoint the refresh token is exchanged against.
_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Keep digests lean — this content lands in the LLM context (same discipline as
# the other lookup tools). Snippets are short; full bodies are truncated hard.
_SNIPPET_CHARS = 200
_BODY_CHARS = 2000


class GmailNotConfigured(RuntimeError):
    """Raised when the Gmail credential env vars are absent. The tool layer
    catches this and returns a clear message rather than crashing the agent."""


def _build_service():
    """Build a readonly Gmail service from the stored refresh token.

    Raises GmailNotConfigured if any credential env var is missing — we do this
    lazily (per call), not at import, so a vault/agent with no Gmail secret
    still starts and every other tool keeps working."""
    client_id = os.getenv("GMAIL_CLIENT_ID")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET")
    refresh_token = os.getenv("GMAIL_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise GmailNotConfigured(
            "Gmail is not configured (missing GMAIL_CLIENT_ID / "
            "GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN)."
        )

    creds = Credentials(
        token=None,  # no access token yet — the library refreshes one
        refresh_token=refresh_token,
        token_uri=_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=GMAIL_SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _header(headers: list[dict], name: str) -> str:
    """Pull a single header value (case-insensitive) from a message's header list."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_part(data: str) -> str:
    """base64url-decode a Gmail MIME part body."""
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")


def _extract_plain_text(payload: dict) -> str:
    """Walk a message payload and return the first text/plain body found.

    Falls back to an empty string (the API `snippet` still covers the digest
    case). We deliberately do NOT pull HTML parts — plain text keeps the LLM
    context clean and small."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/plain" and body.get("data"):
        return _decode_part(body["data"])
    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    return ""


def search_messages(query: str, max_results: int = 10) -> list[dict]:
    """Search the inbox (Gmail query syntax) and return compact per-message
    digests: {id, from, subject, date, snippet}. Capped at `max_results` and
    snippet-truncated — this feeds the LLM, so it stays lean. Read-only."""
    service = _build_service()
    listing = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    ids = [m["id"] for m in listing.get("messages", [])]

    results = []
    for msg_id in ids:
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            .execute()
        )
        headers = msg.get("payload", {}).get("headers", [])
        snippet = (msg.get("snippet", "") or "").strip()
        results.append(
            {
                "id": msg_id,
                "from": _header(headers, "From"),
                "subject": _header(headers, "Subject"),
                "date": _header(headers, "Date"),
                "snippet": snippet[:_SNIPPET_CHARS],
            }
        )
    return results


def get_message(message_id: str) -> dict:
    """Fetch one message's headers + decoded plain-text body (truncated).
    Read-only."""
    service = _build_service()
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    body = _extract_plain_text(payload).strip()
    return {
        "id": message_id,
        "from": _header(headers, "From"),
        "subject": _header(headers, "Subject"),
        "date": _header(headers, "Date"),
        "body": body[:_BODY_CHARS],
    }
