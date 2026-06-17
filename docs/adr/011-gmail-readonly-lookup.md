# ADR 011 — Gmail as a Read-Only Lookup Source

**Date:** 2026-06-16
**Status:** Accepted
**Relates to:** Phase 19, ADR 004 (summary-RAG lookup pattern), ADR 006 (capability gating), ADR 007 (vault is curated)

---

## Context

Athena exposes the user's documents and LeetCode activity as on-demand *lookup
sources*: the agent calls `load_document` / `lookup_leetcode` when a question
needs them, and they do not write anywhere. Phase 19 adds Gmail. Two decisions
need recording, because both are security/scope boundaries that are easy to
erode later:

1. **How much access** the integration gets.
2. **Whether email feeds the memory vault** (like reflection does for
   conversations) or stays a pure lookup.

---

## Decision

### 1. Read-only scope, and nothing wider

The integration requests **`https://www.googleapis.com/auth/gmail.readonly`
only**. The minted OAuth credential is *physically incapable* of sending,
drafting, deleting, modifying, or labeling mail — not "we choose not to," but
"the token cannot." No broader scope is requested "for convenience."

Why:

- **Least privilege on the user's most sensitive account.** Email is identity,
  password resets, and private correspondence. A read-only token cannot be
  abused — by a prompt injection in an email body, by a model mistake, or by a
  leaked secret — to send or destroy anything.
- **Consistent with the project's deferred-writes discipline.** Every write
  surface in Athena is gated and deferred (MCP capability gating, ADR 006;
  explicit-only memory writes, ADR 008). A read-only email surface is the same
  principle: reads are cheap and safe, writes earn their own phase.
- **The boundary is enforced at the credential, not the code.** Even if a future
  code change *tried* to call `messages.send`, Google rejects it for lack of
  scope. The scope is the hard wall; the absence of send/delete/modify calls in
  the code is the second wall.

The scope lives in exactly two places — `agent/gmail_client.py` (runtime) and
`scripts/gmail_oauth.py` (mint) — and is identical in both.

### 2. Gmail is a lookup source, not a memory source (yet)

`search_email` reads mail to answer the *current* question. It does **not** feed
the memory vault, reflection, or the temporal `events` system. The email→memory
loop is a separate, later phase.

Why the boundary now:

- **The vault is curated; the inbox is not.** Phases 14–18 built the vault as a
  deliberately conservative, high-signal store (durable facts only, no PII, no
  trivia — ADR 007/008). Auto-ingesting email would flood it with newsletters,
  receipts, and noise, destroying the property that makes ambient full-vault
  recall (Phase 16) work at all.
- **Email→memory is a filtering-policy problem, not a plumbing problem.** Which
  emails are worth remembering, how to strip PII, how to dedup against existing
  notes — that needs its own design, exactly like auto-capture (Phase 15) was
  designed as a *policy* in chat before it was built. Bolting it onto this phase
  would ship the plumbing without the policy.
- **Keeping them separate keeps this phase shippable and safe.** A pure read
  surface has a small, auditable blast radius; a memory-feed has an open-ended
  one.

### 3. Not on the MCP / tunnel surface this phase

`search_email` is a chat-agent `@tool` only — no `/tools/search_email` endpoint
and no Rust MCP `ToolDefinition`. The other lookups are on the MCP server, which
Phase 13 is prepping for Cloudflare-tunnel exposure; inbox content is more
sensitive than document/LeetCode data, so it stays off the tunnel-facing path
until there's a reason and a review to add it.

---

## Alternatives considered

| Option | Rejected because |
| ------ | ---------------- |
| Request `gmail.modify` (or full scope) "in case we add actions later" | Hands the most dangerous capabilities to a model + a stored token with zero current use. Writes get their own phase and their own gating; mint the narrow scope now, widen only when a write feature is actually designed. |
| Feed matched/important emails into the memory vault this phase | Pollutes the curated vault and skips the filtering-policy design that makes a memory-feed safe. Separate future phase. |
| Build a background email poller / sync | On-demand lookup needs no sync; a poller adds a moving part, a schedule, and a "what do we store" question that belongs to the (deferred) memory-feed phase. |
| Expose `search_email` via the Rust MCP server like the other lookups | Puts inbox content on the LAN/tunnel-facing surface. More sensitive than docs/LeetCode; keep it agent-internal until deliberately reviewed. |
| Store the whole `client_secret.json` + token as one opaque secret blob | Less transparent and harder to rotate piecemeal than three named keys (`GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN`) read straight as env vars. |

---

## Consequences

- **Small, auditable blast radius.** The integration can read mail and nothing
  else; a leaked token or a prompt injection cannot send or destroy.
- **Same lookup shape as the rest of the agent.** `search_email` mirrors
  `lookup_leetcode`/`load_document`: thin client + one lean-digest tool, capped
  and truncated so it stays cheap in the LLM context (ADR 004 lineage).
- **The vault stays clean.** Recall quality (Phase 16) is preserved because the
  inbox doesn't leak into the curated store.
- **The next phase is named, not implied.** Email→memory is explicitly deferred
  with its own filtering-policy design as the gate — not something that quietly
  accretes here.
- **Re-verify on a foreground-model swap is unaffected** (no new prompt-enforced
  capture behavior); the read-only guarantee is enforced by the OAuth scope, not
  the prompt.

---

## Related

- [ADR 004 — Summary-based RAG](004-summary-based-rag.md) — the thin lookup-tool
  shape `search_email` follows.
- [ADR 006 — MCP Auth Granularity](006-mcp-auth-granularity.md) —
  capability/read-vs-write gating discipline.
- [ADR 007 — Agent Memory Vault](007-agent-memory-vault.md) — the vault is a
  curated store; why the inbox must not auto-feed it.
- [ADR 008 — Automatic Memory Capture](008-automatic-memory-capture.md) —
  email→memory would be the same kind of *policy* problem, planned before built.
- [Phase 19 — Gmail read-only lookup](../phases/phase-19-gmail-readonly.md) —
  the implementation this ADR backs.
- `agent/gmail_client.py`, `agent/main.py` (`search_email`),
  `scripts/gmail_oauth.py`, `cluster/agent/gmail-secret.example.yaml`.
