"""Google Calendar client — read + create/update (Phase 23).

Calendar started life as a read-only *lookup source* (Phase 20): the agent
reaches for it to answer "what's on my schedule today?", "when is my next
interview?". Phase 23 widened it so the agent can also CREATE and UPDATE events
on explicit request ("schedule a mock interview Thursday 3pm", "move my dentist
appointment to 4pm").

SECURITY BOUNDARY: the only scope referenced here is `calendar.events`. That
scope is read+write over events and technically permits deletion too — but this
module NEVER deletes: there is no `events().delete()` call anywhere, by
construction. Calendar writes are also FOREGROUND-ONLY at the tool layer
(agent/main.py): the create/update tools live in CHAT_TOOLS, never in the
background reflection path. Keep both invariants — no delete here, no write tool
in reflection.

Credentials come from three env vars (the `gcal-secret` k8s secret in the
`athena` namespace): GCAL_CLIENT_ID, GCAL_CLIENT_SECRET, GCAL_REFRESH_TOKEN.
Phase 23 widened this single secret's token from calendar.readonly to
calendar.events, so the Phase 21 background calendar feed (which only reads via
list_events) now runs on a write-capable token — by design (the user chose to
widen the existing secret rather than mint a separate write credential).
"""

import os
import re

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Read + write events. NOT calendar.readonly anymore (Phase 23). This scope also
# permits delete, which this module deliberately never exercises.
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_TOKEN_URI = "https://oauth2.googleapis.com/token"

_DESCRIPTION_CHARS = 200

# Matches a trailing UTC offset like "+05:30" or "-04:00" on an RFC3339 string.
_OFFSET_RE = re.compile(r"[+-]\d{2}:\d{2}$")


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


def _calendar_timezone(service) -> str:
    """The primary calendar's own IANA timezone, used to interpret naive (no
    offset) datetimes the agent passes when creating/updating events.

    The calendar is the source of truth for the user's timezone, so we read it
    rather than guess. If the call fails (e.g. scope/permission), fall back to
    the CALENDAR_TIMEZONE env, then a sane default — never silently wrong without
    a knob to fix it."""
    try:
        cal = service.calendars().get(calendarId="primary").execute()
        tz = cal.get("timeZone")
        if tz:
            return tz
    except Exception:
        pass
    return os.getenv("CALENDAR_TIMEZONE", "America/New_York")


def _event_dt(value: str, tz: str) -> dict:
    """Build a Calendar start/end object from an agent-supplied datetime string.

    - "YYYY-MM-DD"                -> all-day event ({"date": ...})
    - "...Z" or "...+HH:MM"       -> already has an offset; pass through as-is
    - "YYYY-MM-DDTHH:MM:SS"       -> naive local time; attach the calendar's tz
    """
    v = (value or "").strip()
    if len(v) == 10 and v.count("-") == 2:
        return {"date": v}
    if v.endswith("Z") or _OFFSET_RE.search(v):
        return {"dateTime": v}
    return {"dateTime": v, "timeZone": tz}


def list_events(time_min: str, time_max: str, max_results: int = 10) -> list[dict]:
    """Return events between time_min and time_max (RFC3339 strings).

    Each event is a compact dict: {id, summary, start, end, location,
    description}. The `id` lets the agent chain a lookup into update_event
    (find-then-update, like find_documents -> load_document). Capped at
    max_results and description-truncated — this feeds the LLM context."""
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
                "id": item.get("id", ""),
                "summary": item.get("summary", "(no title)"),
                "start": _format_dt(item.get("start", {})),
                "end": _format_dt(item.get("end", {})),
                "location": item.get("location", ""),
                "description": desc[:_DESCRIPTION_CHARS],
            }
        )
    return events


def _digest(event: dict) -> dict:
    """Compact dict for a created/updated event (mirrors list_events shape +
    the htmlLink so the agent can hand the user a clickable confirmation)."""
    return {
        "id": event.get("id", ""),
        "summary": event.get("summary", "(no title)"),
        "start": _format_dt(event.get("start", {})),
        "end": _format_dt(event.get("end", {})),
        "location": event.get("location", ""),
        "htmlLink": event.get("htmlLink", ""),
    }


def create_event(
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    """Create an event on the primary calendar and return its compact digest.

    `start`/`end` accept "YYYY-MM-DD" (all-day) or "YYYY-MM-DDTHH:MM:SS" (timed,
    interpreted in the calendar's timezone) or a fully-offset RFC3339 string.
    Write-capable; never deletes."""
    service = _build_service()
    tz = _calendar_timezone(service)
    body: dict = {
        "summary": summary,
        "start": _event_dt(start, tz),
        "end": _event_dt(end, tz),
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    created = service.events().insert(calendarId="primary", body=body).execute()
    return _digest(created)


def update_event(
    event_id: str,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> dict:
    """Patch an existing event by id (partial update — only provided fields
    change) and return its compact digest. Caller must supply a real event_id
    (get it from list_events). Write-capable; never deletes."""
    service = _build_service()
    body: dict = {}
    if summary is not None:
        body["summary"] = summary
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if start is not None or end is not None:
        tz = _calendar_timezone(service)
        if start is not None:
            body["start"] = _event_dt(start, tz)
        if end is not None:
            body["end"] = _event_dt(end, tz)
    if not body:
        raise ValueError("update_event called with no fields to change.")
    updated = (
        service.events()
        .patch(calendarId="primary", eventId=event_id, body=body)
        .execute()
    )
    return _digest(updated)
