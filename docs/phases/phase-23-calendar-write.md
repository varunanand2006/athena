# Phase 23: Calendar create + update (foreground writes)

**Status:** Implemented, pending cluster rollout
**Depends on:** Phase 20 (read-only Calendar lookup), Phase 21 (foreground vs.
background write discipline, `update_memory`)

## Goal

Turn Calendar from a read-only lookup source into one Athena can also **write**
to — but narrowly. On an explicit request the agent can **create** a new event
("schedule a mock interview Thursday 3pm") and **update/reschedule** an existing
one ("move my dentist appointment to 4pm"). It **cannot delete** events, and it
**cannot do anything autonomously** — writes only ever happen in the foreground
chat path on an explicit user instruction.

Gmail is explicitly untouched this phase: it stays **read-only**. Drafting and
labeling were considered and dropped because no Gmail OAuth scope grants drafts
or message-labeling without also granting *send*, and the requirement was no
sending whatsoever — a guarantee we'd only be able to enforce in code, not at
the credential. Not worth the broadened token right now.

## Design

### Scope widened in place (calendar.events)

The existing `gcal-secret` token was widened from `calendar.readonly` to
**`https://www.googleapis.com/auth/calendar.events`** (read + create/update +
— unused — delete). We deliberately **widened the existing secret** rather than
mint a separate write credential, so all calendar code (the `get_calendar_events`
lookup, the new write tools, and the Phase 21 background calendar feed) shares
one token. Consequence, accepted by choice: the background calendar feed —
read-only *by behavior* (it only calls `list_events`) — now runs on a
write-capable token.

The scope is hardcoded in the same two places as before:
`agent/calendar_client.py` (runtime) and `scripts/calendar_oauth.py` (token
mint). **Re-minting is required** — the old read-only refresh token cannot
create or update events. Re-run `scripts/calendar_oauth.py`, replace the value in
`gcal-secret`, `kubectl apply`, rollout-restart.

### No delete, by construction

`calendar.events` permits deletion, but `calendar_client.py` has **no
`events().delete()` call anywhere** — the "no delete" guarantee lives in code,
not in the scope. The `update_calendar_event` tool's prompt also tells the model
to refuse deletion requests and offer reschedule/edit instead.

### Foreground-only, by construction

The two write tools (`create_calendar_event`, `update_calendar_event`) are in
`CHAT_TOOLS` only — the gpt-4o-mini chat agent the user is actively talking to.
Background reflection (`reflection.py`) never receives them, exactly like
`update_memory`'s destructive `replace_events` is foreground-only. The system
prompt enforces "explicit request only — never create or change events on your
own initiative." **Re-verify this on any foreground-model swap**, same discipline
as the explicit-only `write_memory`/`update_memory` rules.

### Client functions

`agent/calendar_client.py`:
- `create_event(summary, start, end, description=None, location=None)` →
  `events().insert` → compact digest `{id, summary, start, end, location,
  htmlLink}`.
- `update_event(event_id, summary=None, start=None, end=None, description=None,
  location=None)` → `events().patch` (partial — only provided fields change).
- `list_events(...)` now also returns each event's `id` so the agent can do the
  **find-then-update** two-step (mirrors `find_documents` → `load_document`).

### Timezone handling

`start`/`end` accept `"YYYY-MM-DD"` (all-day), a naive local
`"YYYY-MM-DDTHH:MM:SS"`, or a fully-offset RFC3339 string. For naive local
times we attach a timezone read from **the primary calendar itself**
(`calendars().get('primary').timeZone`) — the calendar is the source of truth, so
we never guess the user's tz. If that lookup fails, we fall back to the
`CALENDAR_TIMEZONE` env (default `America/New_York`). Relative dates ("tomorrow",
"next Friday 3pm") are resolved by the model against today's date, which the chat
path already stamps at the top of the prompt (added in Phase 17/21 for memory
events).

### Two-step reschedule

To change an event the agent first calls `get_calendar_events` (now showing each
event's `[id: ...]`), then passes that id to `update_calendar_event`. Keeping
"find the event" and "change the event" as separate tools mirrors the existing
two-step retrieval pattern.

## OAuth setup (re-mint required)

Reuse the same Desktop-app OAuth client as Gmail/Calendar read-only; only the
requested scope changes.

```
pip install google-auth-oauthlib          # laptop-only dep, not in the image
python scripts/calendar_oauth.py /path/to/client_secret_XXX.json
# browser opens; consent to Calendar read + create/update access
```

Then replace the refresh token in the secret (note the `-n athena` requirement):

```
# paste the new GCAL_* values into cluster/agent/gcal-secret.yaml, then:
kubectl apply -n athena -f cluster/agent/gcal-secret.yaml
```

## Deployment (per CLAUDE.md image workflow)

The agent runs on **xdev-sr** (`workload: ai`), so build + import there:

```
# on xdev-sr, in the agent/ dir
sudo docker build -t athena-agent:phase23 .
sudo docker save -o /tmp/athena-agent.tar athena-agent:phase23   # no gzip
sudo chmod 644 /tmp/athena-agent.tar
sudo k3s ctr images import /tmp/athena-agent.tar                  # k3s ctr, NOT plain ctr

# from vlinux1 or the laptop (vlinux2 has no kubeconfig)
kubectl apply -n athena -f cluster/agent/gcal-secret.yaml         # re-minted token
kubectl apply -n athena -f cluster/agent/deployment.yaml          # image bumped to :phase23, + CALENDAR_TIMEZONE
kubectl rollout restart -n athena deployment/agent
```

Set `CALENDAR_TIMEZONE` in the deployment to the user's IANA timezone (it is only
a fallback — the runtime prefers the calendar's own timezone).

## Phase gate (testable)

1. In chat: "Schedule a mock interview tomorrow 3-4pm." → agent calls
   `create_calendar_event` with correctly-resolved ISO times, the event appears on
   the real calendar, and the reply confirms the resolved date/time.
2. "Move that to 5pm." → agent calls `get_calendar_events` to find the id, then
   `update_calendar_event`; the event time changes on the calendar.
3. "Delete it." → agent refuses deletion and offers to reschedule/edit instead.
4. Confirm via code + scope that there is no `events().delete()` path and that the
   write tools are absent from the background reflection path.

## Out of scope (explicitly NOT this phase)

- **Event deletion / cancellation.** No delete tool; refused in-prompt.
- **Gmail writes (drafts, labels, send).** Gmail stays read-only — see Goal.
- **Background/autonomous event creation.** Foreground, explicit-request only.
- **Attendees / invitations / free-busy scheduling.** Later, if wanted.
- **Exposure via the Rust MCP server.** Like the read-only Google tools, the
  calendar write tools are chat-agent only — kept off the tunnel-facing surface.
