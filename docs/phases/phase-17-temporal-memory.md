# Phase 17: Temporal Frontmatter on Memory Notes

**Status:** Complete (gate passed)
**Depends on:** Phase 14 (vault substrate), Phase 15 (auto capture), Phase 16 (recall)

## Goal

Let memory answer time-based questions ("what's coming up this week?") without
adding a second store. Single principle: **the vault stays the one source of
truth**; a note that involves a date carries that date in its own YAML
frontmatter, not in a separate table.

## The one-store decision

There is deliberately **no facts table and no parallel store**. The date lives
inside the note, so there is exactly one record: delete the note and the event
is gone; edit the date and it's edited everywhere. We rejected a separate
events/facts row because dual-store sync (a note plus a row that can drift on
edit/delete) is a maintenance tax we won't pay. Full reasoning in
[ADR 009](../adr/009-temporal-frontmatter.md).

We are also **not** classifying memories as "fact vs prose." Factness depends on
the future query, not the content, so that's unanswerable. Every memory stays
prose; we only extract **one** queryable attribute — a date — into frontmatter
when present. Reflection's decision is the narrow, mechanical "does this note
contain a date/deadline?", never the philosophical "is this a fact?". Getting it
wrong is cheap and recoverable: a missed date just means the note isn't
time-queryable (it still exists as prose), and `events` is *derived* —
rebuildable by re-scanning the vault — so early extraction mistakes are never
permanent. This is the same "thin queryable index in front of rich content"
pattern as summary-RAG ([ADR 004](../adr/004-summary-based-rag.md)).

## Architecture

### Note format extension (agent/memory.py)

A new optional `events` frontmatter field: a YAML flow list of
`{date: YYYY-MM-DD, kind: <short string>}` maps, e.g.

```yaml
events: [{date: 2026-06-19, kind: interview}, {date: 2026-07-01, kind: deadline}]
```

- `parse_note` / `_render_note` / `read_note` / `list_notes` / `write_note` all
  carry it.
- Missing `events` defaults to `[]` — every pre-Phase-17 note is dateless by
  definition (same defaulting discipline as the `source` field in Phase 15).
- Hand-rolled `_parse_events` / `_format_events` (no PyYAML, matching the
  existing tags approach). Tolerant: only maps with a non-empty `date` survive;
  malformed entries are dropped.
- `_merge_events` unions events across same-slug updates, deduping on
  `(date, kind)` — exactly how tags are merged, so re-reflection adds new
  deadlines without dropping or duplicating old ones.
- Stays Obsidian-compatible — it's just valid YAML flow frontmatter.

### Reflection captures dates (agent/reflection.py)

The reflection prompt is extended so that when it captures/updates a note about
something time-bound, it also emits the date(s) as structured `events` entries.
The conservative-capture policy (durable facts, no PII) is unchanged.

Crucially, the prompt now:

- States **today's date** so relative dates are resolvable.
- Instructs the model to capture **only concrete, resolved calendar dates** —
  "next Friday" must be worked out to an actual `YYYY-MM-DD`.
- Says that vague/unresolvable timing ("sometime soon", "in a few weeks") is
  left as prose only, never forced into a malformed event.

On the way back in, `_sanitize_events()` validates every model-emitted event:
only items whose `date` parses as ISO `YYYY-MM-DD` survive (kind coerced to a
short string). A malformed/unresolvable date is dropped rather than written as a
broken event — keeping "getting it wrong" cheap and recoverable. Validated
events are passed to `write_note(..., events=...)`.

### `upcoming(timeframe)` tool (agent/main.py)

A new `@tool` for time-recall:

- Scans **all** notes' `events` frontmatter via `memory_vault.collect_events()`
  — deliberately the same full-vault-scan pattern as Phase 16's full-vault load.
- `timeframe` accepts "today", "tomorrow", "week" (default, next 7 days),
  "month" (next 30 days), or "next N days" (`_resolve_window_days`).
- Returns events within `[today, today + window]`, each with its note title and
  kind, **sorted by date**. Malformed dates are skipped.
- Registered in the react agent's tool list; the system prompt directs
  time-based questions ("what's coming up this week?", "any deadlines soon?") to
  call `upcoming` and answer from the dated events, not keyword-match memory
  text.

**Tripwire** (consistent with Phase 16's cap): if the scan exceeds
`MEMORY_EVENTS_MAX_NOTES` (env, default 500) it logs a clear "vault too big for
frontmatter scan — time for a derived index" `WARNING`. We do **not** build a
derived index now — just the honest tripwire.

### Frontend (frontend/MemoryView.tsx)

Read-only, consistent with the existing view:

- An `EventChip` (📅 date · kind, green) renders a note's events in the detail
  view next to its tags.
- A new **Upcoming** panel in the left column lists events across all notes,
  today-or-later, soonest first; each row links to its note. This mirrors the
  agent's `upcoming()` tool over the same frontmatter.

## No database / dependency changes

`events` lives in note frontmatter — the one-store decision means no migration,
no new table, no new Python dependency.

## Gate Checklist

1. **Date capture via reflection** ✅ — mentioning something dated without an
   explicit "remember" (e.g. "my Stripe interview is next Friday") then starting
   a new conversation (so reflection fires) results in the relevant note gaining
   an `events: [{date: <resolved YYYY-MM-DD>, kind: interview}]` entry — verified
   in the vault and the /memory view's event chip.
2. **Time-recall by date, not keyword** ✅ — asking "what's coming up this week?"
   has the agent call `upcoming` and answer by date, returning the Stripe
   interview.
3. **Dateless notes unaffected** ✅ — a pure-prose memory with no date produces
   no `events` entry and is untouched.

## Known Limitations & Future Work

- **`upcoming` is a linear full-vault scan.** Fine at this scale; the
  `MEMORY_EVENTS_MAX_NOTES` tripwire is the named signal to build a derived
  index later.
- **Date resolution is model-dependent.** Reflection (gemma4:e2b) must do the
  "next Friday → YYYY-MM-DD" arithmetic. `_sanitize_events` guards against
  malformed output, but a wrong-but-valid date is possible; the note prose
  retains the original phrasing, and `events` is rebuildable by re-scanning.
- **No calendar export.** Pushing events to Google Calendar is a much-later
  downstream export, explicitly out of scope here (see ADR 009).
- **No episodic/conversation recall.** Still out of scope — a later phase.

## Docs

- [ADR 009](../adr/009-temporal-frontmatter.md) — temporal frontmatter design
- Previous: [Phase 16](phase-16-memory-recall.md), [Phase 15](phase-15-auto-memory.md)
- [ADR 007](../adr/007-agent-memory-vault.md) — vault substrate
