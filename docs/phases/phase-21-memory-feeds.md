# Phase 21: Safe memory correction + external source feeds

**Status:** Implemented (gates pending cluster deploy)
**Depends on:** Phase 14 (vault), Phase 15 (auto capture), Phase 17 (events),
Phase 18 (wiki graph), Phase 19 (Gmail read-only), Phase 20 (Calendar read-only)

## Goal

Two related extensions to *how memory gets written*. Neither changes the vault
format beyond adding one provenance field; both build on the existing
`write_note` / reflection paths.

1. **Safe in-chat memory correction** (foreground, gpt-4o-mini, user-initiated)
   — a clean way to *update* an existing note's body and dated events on an
   explicit correction, instead of appending a contradiction.
2. **Automatic external source feeds** (background, gemma, reflection-triggered)
   — Google Calendar (fully automatic) and label-filtered Gmail (curated by the
   user) flow into the vault on the same reflection triggers, tagged with where
   they came from.

See [ADR 012](../adr/012-external-memory-feeds.md) for the decision record.

---

## Part 1 — Safe in-chat memory correction

### The problem

`write_memory` always **appends**. If the user says "my Stripe interview moved
to Tuesday", the old append-only path bolts a contradiction onto the existing
note (and, with events, leaves *both* Monday and Tuesday in the frontmatter).
There was no foreground path to cleanly *correct* a note.

### What was built

- **`memory.write_note(..., replace_events=...)`** — a new flag parallel to the
  Phase 18 `replace` (body) flag. `replace_events=True` makes the supplied
  events **replace** the note's existing events instead of unioning with them.
  `replace` (body) + `replace_events` (events) together give a full correction.
- **`update_memory` tool** (`agent/main.py`) — foreground-only tool that calls
  `write_note(..., replace=True, replace_events=True, source="explicit")`. It
  replaces the body with the corrected content and replaces the dated events, so
  the correction stands alone with no stale date left behind.
- **`write_memory` now accepts `events`** — so an explicit "remember my Stripe
  interview is Monday" records the date in frontmatter (previously only
  background reflection emitted events). The chat system prompt now carries
  today's date so the model can resolve "Monday" / "next Friday" to a real
  `YYYY-MM-DD`.
- **`memory.sanitize_events`** — the event-validation logic (keep only real ISO
  dates) was promoted to `memory.py` as the single source of truth, shared by
  `write_memory`, `update_memory`, and both background sweeps. `reflection._sanitize_events`
  is now a thin shim over it.

### The boundary (prompt-enforced — re-verify on any foreground model swap)

The system prompt draws a hard line, in the same spirit as the Phase 14
explicit-only `write_memory` rule:

- **`update_memory`** fires ONLY on explicit *correction* language applied to
  something already in memory — "update…", "change…", "correct…", "actually
  it's…", "moved to…", "reschedule…", "no longer…".
- **New, non-contradicting information stays append-only** — the model does
  nothing (background capture handles it); it must NOT use `update_memory` to
  add new facts.

### Safety constraint

The replace/correction capability is **foreground-only**. The agent is
gpt-4o-mini, the user is watching, and the trigger is an explicit correction —
that's the version that earns a destructive rewrite. **Background reflection
(gemma) never passes `replace_events`** and is unchanged by Part 1; the Phase 18
concern about unattended gemma rewrites still stands and is deliberately *not*
resolved for the background path here. A background condense/replace pass on a
better model is a separate future phase.

### Part 1 gate

1. Chat: "remember my Stripe interview is Monday" → a note with a Monday event.
2. "actually my Stripe interview moved to Thursday" → the agent calls
   `update_memory`; the note's event frontmatter shows **Thursday only** (not
   Monday + Thursday), and the body reflects the correction, not a
   contradiction.
3. A genuinely new fact ("remember I'm also learning Rust") still **appends** /
   creates a separate note.

---

## Part 2 — Automatic external source feeds

### The split: calendar auto, email label-gated

- **Calendar — fully automatic.** Calendar events are curated *by definition*:
  the user deliberately put them there. Background reflection sweeps upcoming
  events (next 14 days) and captures the durable ones as notes / events
  frontmatter. Low noise, high signal.
- **Email — label-filtered only.** Reflection ingests **only** emails carrying a
  specific Gmail label (default `athena`, env `ATHENA_EMAIL_LABEL`). The user
  applies the label in Gmail; reflection picks it up on the next sweep. The user
  stays the curator — automation is only the *timing*. **The full inbox is NEVER
  swept** — this is non-negotiable and out of scope for any future phase unless
  separately designed and gated.

### Provenance: the `origin` field

Notes/updates written by a sweep carry `source: auto` (like conversation
reflection) **plus** a new `origin` frontmatter field: `calendar`, `email`, or
`conversation` (default for every pre-existing note — backward compatible).
`origin` is preserved across updates (a property of the first write, like
`source`). Surfaced in `/memory` as a "from calendar / from email / from
conversation" chip so the vault is auditable and a wrong feed-capture is one
click to delete.

`parse_note` / `_render_note` / `read_note` / `list_notes` / `write_note` all
carry `origin`; no other format change.

### Conservative capture, append-only

Both sweeps reuse the Phase 15 conservative policy (durable facts only, no
PII/credentials, no transient content; the model decides, the prompt guides it
to be conservative) and write **append-only** via `_apply_feed_decisions` —
feeds do **not** get `replace=True` or author concept-page rewrites, keeping
unattended gemma writes non-destructive.

### Watermarks / idempotency

- **Calendar** — a self-contained `_calendar_sweep.md` watermark in the vault
  records the last run; the sweep is throttled to at most once every
  `CALENDAR_SWEEP_MIN_INTERVAL_HOURS` (default 6) so it doesn't re-run the LLM on
  every new-conversation boundary. Event-level idempotency comes from the
  same-slug + event-dedup merge.
- **Email** — a Postgres `email_processed(message_id, label, processed_at)`
  table. Message IDs are structured data, not memory, so Postgres is the right
  home (cleaner than a vault file). Every *considered* message is marked
  processed — even ones that produced no memory — so "considered and skipped" is
  never re-evaluated. Created by `migrate.sql` and defensively via
  `CREATE TABLE IF NOT EXISTS` in the sweep.

### Triggers

Both sweeps run via `_run_external_feeds()` at the **same** triggers as
conversation reflection: the new-conversation boundary (background thread) and
the 30-minute straggler APScheduler job. Each source is isolated in its own
try/except so one failure never affects the other or conversation reflection.

### Graceful degradation

Each sweep catches its `*NotConfigured` exception and **silently skips** if the
`gcal-secret` / `gmail-secret` isn't mounted (the existing `optional: true`
secret pattern). The agent starts and runs normally with neither credential.

### Part 2 gate

1. **Calendar** — add a real upcoming event; start a new conversation (boundary
   sweep). The event appears as a note / events update with `source: auto`,
   `origin: calendar`, visible in `/memory` with a "from calendar" chip.
2. **Email** — apply the `athena` label to a real email; start a new
   conversation. Its key info appears as a note with `source: auto`,
   `origin: email`, "from email" chip; the message ID is marked processed and is
   NOT re-captured on the next sweep.

---

## Explicitly NOT in this phase

- Sweeping the full inbox (the label filter is mandatory, now and later).
- Giving background reflection `replace=True`/`replace_events` (foreground-only).
- Any write/send/delete Gmail or Calendar operation (read-only scopes only).
- Changing the vault format beyond adding `origin`.
- A separate events/facts table (the one-store decision from ADR 009 stands).

## Deployment

Standard agent image workflow (build on xdev-sr, `k3s ctr` import, bump the YAML
tag — now `:phase21`):

```
# on xdev-sr, in agent/
sudo docker build -t athena-agent:phase21 .
sudo docker save -o /tmp/athena-agent.tar athena-agent:phase21    # no gzip
sudo chmod 644 /tmp/athena-agent.tar
sudo k3s ctr images import /tmp/athena-agent.tar                   # k3s ctr, NOT plain ctr

# from vlinux1 or laptop (vlinux2 has no kubeconfig)
psql ... -f scripts/migrate.sql                                   # adds email_processed
kubectl apply -n athena -f cluster/agent/deployment.yaml          # image -> :phase21
kubectl rollout restart -n athena deployment/agent
```

Frontend (origin chip) rebuilds via the usual Vite → nginx image on vlinux2.

The `gmail-secret` / `gcal-secret` are unchanged from Phases 19/20 (read-only
scopes); no new secret. Apply the `athena` label in Gmail to opt an email in.

## Known limitations & future work

- **Background condense/replace** — feeds and reflection remain append-only; a
  background reconcile on a better model is deferred (Part 1 only resolves the
  foreground correction path).
- **Calendar idempotency is merge-based** — re-runs rely on same-slug + event
  dedup rather than per-event IDs; the throttle keeps churn low.
- **Prompt-enforced boundaries** (foreground correction discipline, recall
  policy, explicit-only write) remain model-dependent — re-verify on a model
  swap.

## Docs

- [ADR 012](../adr/012-external-memory-feeds.md) — external-memory-feeds design
- Previous: [Phase 19](phase-19-gmail-readonly.md) (Gmail), Phase 20 (Calendar)
- [ADR 007](../adr/007-agent-memory-vault.md), [ADR 008](../adr/008-automatic-memory-capture.md)
