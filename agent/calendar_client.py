"""Thin read-only Google Calendar client (Phase 20).

Calendar is a *lookup source* — the agent reaches for it to answer questions
like "what's on my schedule today?", "do I have anything with <person>?",
"when is my next interview?". Same discipline as gmail_client.py and
load_document: on-demand, lean digest, never dumps the full calendar.

HARD SECURITY BOUNDARY: the only scope referenced anywhere in this module is
`calendar.readonly`. The minted credential is physically incapable of creating,
editing, or deleting events — there are no such calls here, by construction.

Credentials come from three env vars (a k8s secret in the `athena` namespace):
GCAL_CLIENT_ID, GCAL_CLIENT_SECRET, GCAL_REFRESH_TOKEN.
"""

import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

_TOKEN_URI = "https://oauth2.googleapis.com/token"

_DESCRIPTION_CHARS = 200


class CalendarNotConfigured(RuntimeError):
    """Raised when the Calendar credential env vars are absent."""


def _build_service():
    client_id = os.getenv("GCAL_CLIENT_ID")
    client_secret = os.getenv("GCAL_CLIENT_SECRET")
    refresh_token = os.getenv("GCAL_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise CalendarNotConfigured(
            "Google Calendar is not configured (missing GCAL_CLIENT_ID / "
            "GCAL_CLIENT_SECRET / GCAL_REFRESH_TOKEN)."
        )
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=CALENDAR_SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _format_dt(dt_field: dict) -> str:
    """Return a human-readable string from a Calendar dateTime or date field."""
    if not dt_field:
        return ""
    if "dateTime" in dt_field:
        return dt_field["dateTime"]
    return dt_field.get("date", "")


def list_events(time_min: str, time_max: str, max_results: int = 10) -> list[dict]:
    """Return upcoming events between time_min and time_max (RFC3339 strings).

    Each event is a compact dict: {summary, start, end, location, description}.
    Capped at max_results and description-truncated — this feeds the LLM context.
    Read-only."""
    service = _build_service()
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = []
    for item in result.get("items", []):
        desc = (item.get("description") or "").strip()
        events.append(
            {
                "summary": item.get("summary", "(no title)"),
                "start": _format_dt(item.get("start", {})),
                "end": _format_dt(item.get("end", {})),
                "location": item.get("location", ""),
                "description": desc[:_DESCRIPTION_CHARS],
            }
        )
    return events
