# ADR 012 — External memory feeds + foreground-only correction

**Date:** 2026-06-19
**Status:** Accepted
**Relates to:** Phase 21; ADR 007 (vault), ADR 008 (auto capture), ADR 009
(temporal frontmatter / one-store), ADR 010 (wiki graph), ADR 011 (Gmail
read-only)

---

## Context

Phases 19–20 made Gmail and Calendar **on-demand lookup sources** — the agent
reaches for them when asked, and they deliberately do **not** feed memory.
Phase 21 adds the controlled path from those sources *into* the vault, plus a
foreground way to *correct* memory rather than only append. Four decisions
shape it.

---

## Decision 1 — Calendar is automatic; email is label-gated

The two sources are fed differently **on purpose**:

- **Calendar → fully automatic.** A calendar entry is already a deliberate user
  act — the user curated it by putting it there. Sweeping upcoming events is
  therefore low-noise and high-signal, and needs no second curation gate.
- **Email → label-filtered only.** An inbox is the opposite: mostly noise the
  user did not choose. So reflection ingests **only** emails the user
  hand-labels (`athena` by default). The label *is* the curation; automation
  only changes the timing of capture, not the decision to capture.

**The full inbox is never swept** — this is a hard constraint, not a default.
Full-inbox auto-capture would pollute the curated vault with marketing,
receipts, and threads the user never treated as significant, and would put PII
into a PVC at scale. Out of scope here and in any future phase unless separately
designed and gated.

**Alternative considered:** sweep both automatically. Rejected — the inbox has
no curation signal; without the label gate the conservative-capture prompt would
be the *only* defense against noise, which is too weak a guarantee for a store
the user is meant to trust.

---

## Decision 2 — The replace/correction capability is foreground-only

`update_memory` (and the underlying `replace_events`) can **destroy** prior
memory content: it overwrites a note's body and replaces its dated events. That
capability is granted **only** to the foreground path:

- Foreground is **gpt-4o-mini**, the user is **watching**, and the trigger is an
  **explicit correction** ("moved to Thursday"). Mislabeled or wrong rewrites
  are immediately visible and reversible in-conversation. That combination is
  what earns a destructive write.
- **Background reflection (gemma) does not get it.** An unattended small CPU
  model silently dropping a good date or rewriting a note wrong is exactly the
  Phase 18 concern; this phase resolves correction for the foreground only.
  Feeds and reflection stay **append-only** (no `replace_events`, no concept
  rewrites in the feed path). A background condense/replace on a stronger model
  is a separate, separately-gated future phase.

This mirrors the Phase 14 explicit-only `write_memory` discipline: a powerful
write is unlocked by an explicit, attended, human-in-the-loop trigger — and is
**prompt-enforced**, so it must be re-verified on any foreground-model swap.

---

## Decision 3 — `origin` provenance frontmatter

Every note gains an `origin` field: `conversation` (default), `calendar`, or
`email`. It answers "where did this come from?" — distinct from `source`
(`explicit` vs `auto`, i.e. *who* wrote it).

Why a field and not inference:

- **Auditability is the trust valve.** Now that calendar/email write
  autonomously, the user must be able to *see* a feed-captured note and delete
  it if wrong. `origin` drives a `/memory` chip ("from calendar / from email")
  exactly as `source` drives the auto/you badge in Phase 15. Both are
  load-bearing, not cosmetic.
- **Cheap and backward-compatible.** It's one scalar in the existing hand-rolled
  frontmatter; missing → `conversation`, so every pre-Phase-21 note is correct
  without a migration. Preserved across updates (a property of the first write,
  like `source`), so the chip stays honest when a feed later touches a note.

No separate provenance table — consistent with ADR 009's one-store rule (the
note is the single record).

---

## Decision 4 — Watermark per source, not a shared one

The two sweeps track "what's done" differently, matched to their data shape:

- **Calendar — a `_calendar_sweep.md` last-run timestamp in the vault.** The
  watermark only needs to *throttle* (don't re-run the LLM every conversation);
  per-event idempotency already comes free from same-slug + event-dedup merge.
  A self-contained vault file keeps the calendar path dependency-free and
  Obsidian-visible (`_`-prefixed, so `list_notes` skips it — same convention as
  `_index.md` / the documents TOC).
- **Email — a Postgres `email_processed(message_id, …)` table.** Message IDs are
  **structured data, not memory**, and exactly-once matters (a labeled email
  must capture at most once, never re-evaluated). A keyed table is the right
  tool; a vault file would be an abuse of the markdown store. Every *considered*
  message is marked processed, even if it yielded no memory, so
  "considered-and-skipped" is never reprocessed.

**Alternative considered:** a single shared `*_reflected_at` column like
conversations. Rejected — calendar has no stable per-row identity to watermark
against (events shift), and email needs per-message exactly-once; one mechanism
fits neither well.

---

## Consequences

**Positive**
- The vault compounds from curated external signal (calendar + labeled email),
  not just conversations, while staying low-noise.
- The user can correct memory in-chat without leaving contradictions behind.
- Every note is auditable by `source` + `origin`; wrong captures are one delete.
- Both sweeps degrade silently without their Google credential — the agent runs
  fine with neither.

**Negative / trade-offs**
- More background LLM load (two extra gemma passes), bounded by the calendar
  throttle and the email label filter + processed-ledger.
- Correction quality and the append-vs-correct boundary are **prompt-enforced**
  on gpt-4o-mini — re-verify on a model swap.
- Calendar idempotency is merge-based, not per-event-ID — acceptable at this
  scale; the throttle keeps churn low.

---

## Related

- [ADR 008](008-automatic-memory-capture.md) — conservative capture policy,
  `source` field, foreground explicit-only discipline (extended here).
- [ADR 009](009-temporal-frontmatter.md) — one-store rule (no events/facts
  table) upheld; `origin` follows the same "the note is the record" reasoning.
- [ADR 011](011-gmail-readonly-lookup.md) — the read-only scopes these feeds
  reuse; this ADR is the deliberately-separate "email → memory" phase it deferred.
- [Phase 21 write-up](../phases/phase-21-memory-feeds.md).
