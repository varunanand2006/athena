# Phase 19: Gmail read-only lookup

**Status:** Complete (gate passed)
**Depends on:** Phase 11 (summary-RAG lookup pattern), Phase 12 (direct tool surface)

## Goal

Give Athena a fourth on-demand **lookup source**: the user's Gmail inbox,
**read-only**. The agent can search and read mail to answer questions like "did
the recruiter reply?", "what did Stripe say?", "find the email about my
internship offer" — and nothing more. This mirrors the existing lookup tools
(`load_document`, `lookup_leetcode`): a queryable source the agent reaches for
when asked, **not** a source that auto-feeds the memory vault.

Email → memory is a deliberately separate future phase (see *Out of scope*).

## Design (see [ADR 011](../adr/011-gmail-readonly-lookup.md))

### Read-only, by construction

The OAuth scope is **`https://www.googleapis.com/auth/gmail.readonly` only**.
The minted credential is *physically incapable* of sending, drafting, deleting,
modifying, or labeling mail — there is no broader scope requested "for
convenience," and there is no send/draft/delete/modify/label call anywhere in
the code. The scope is hardcoded in exactly two places: `agent/gmail_client.py`
(runtime) and `scripts/gmail_oauth.py` (token mint). This matches the
project-wide discipline — writes are gated and deferred; this is a pure read
surface.

### A thin client, same shape as the other lookups

`agent/gmail_client.py` is a thin read wrapper over the official Google API
client:

- Builds credentials from three env vars (`GMAIL_CLIENT_ID`,
  `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`) via
  `google.oauth2.credentials.Credentials`; the library transparently mints
  short-lived access tokens from the long-lived refresh token on each call.
- `search_messages(query, max_results=10)` → `users().messages().list(q=...)`
  then a `format=metadata` `get` per hit → compact dicts
  `{id, from, subject, date, snippet}`.
- `get_message(id)` → headers + decoded plain-text body (truncated).
- A lazy not-configured guard (`GmailNotConfigured`): if any env var is missing
  the client raises a clear error instead of crashing the agent at import — so
  the agent runs fine before the secret is applied.

### One agent tool, lean digest

`search_email(query)` (`agent/main.py`, registered in `create_react_agent`)
calls `gmail_client.search_messages`, caps at **10** messages, and formats a
one-line-per-message digest (sender, subject, date, truncated snippet) — the
same context discipline as `lookup_leetcode`/`load_document`. It does **not**
dump full inboxes. The system prompt tells the model it can search email
read-only for "did X reply / what did the recruiter say / find the email about
Y" questions, and that it **cannot** send, draft, reply, delete, or label.

Not exposed via the Rust MCP server this phase — kept off the tunnel-facing
surface; it's a chat-agent tool only.

## OAuth setup (one-time)

The GCP **project-creation** quota is blocked, so reuse an **existing** GCP
project — enabling an API in an existing project is unaffected by that quota.

1. **Enable the Gmail API** in the existing project (APIs & Services → Library →
   Gmail API → Enable).
2. **Configure the OAuth consent screen** (External is fine for a personal
   account). Add your Google account as a **test user** so consent works without
   app verification.
3. **Create OAuth credentials** of type **Desktop app** (APIs & Services →
   Credentials → Create credentials → OAuth client ID → Desktop app). Download
   the client JSON (`client_secret_XXX.json`).
4. **Mint the refresh token locally** (on your laptop, not in-cluster):
   ```
   pip install google-auth-oauthlib          # laptop-only dep, not in the image
   python scripts/gmail_oauth.py /path/to/client_secret_XXX.json
   ```
   A browser opens; consent to **read-only** Gmail access. The script prints the
   client id, client secret, and refresh token.
5. **Populate and apply the secret** (note the `-n athena` requirement):
   ```
   cp cluster/agent/gmail-secret.example.yaml cluster/agent/gmail-secret.yaml
   # paste the three values into stringData, then:
   kubectl apply -n athena -f cluster/agent/gmail-secret.yaml
   ```
6. **Build / import / roll out** the agent image (see Deployment below).

The downloaded `client_secret_*.json` and the populated `gmail-secret.yaml` are
gitignored — never commit them.

## Deployment (per CLAUDE.md image workflow)

The agent runs on **xdev-sr** (`workload: ai`), so build + import there:

```
# on xdev-sr, in the agent/ dir
sudo docker build -t athena-agent:phase19 .
sudo docker save -o /tmp/athena-agent.tar athena-agent:phase19   # no gzip
sudo chmod 644 /tmp/athena-agent.tar
sudo k3s ctr images import /tmp/athena-agent.tar                  # k3s ctr, NOT plain ctr

# from vlinux1 or the laptop (vlinux2 has no kubeconfig)
kubectl apply -n athena -f cluster/agent/gmail-secret.yaml        # if not already applied
kubectl apply -n athena -f cluster/agent/deployment.yaml          # image bumped to :phase19
kubectl rollout restart -n athena deployment/agent
```

The `gmail-secret` env vars are wired with `optional: true`, so the pod starts
even before the secret exists; `search_email` returns "not configured" until it
does.

## Phase gate (single, testable)

With a real email in the account, ask in chat:

> "Did I get any email from \<known sender\>, and what did it say?"

→ the agent calls `search_email`, returns the real matching message's
sender/subject/snippet, and answers from it. Confirm via the code and the OAuth
scope that **no** send/delete/modify capability exists, and that the refresh
token lives in an `athena`-namespace secret, uncommitted.

## Out of scope (explicitly NOT this phase)

- **Email → memory vault / reflection.** Email auto-flowing into the curated
  vault would pollute it; that needs its own filtering-policy design — a separate
  future phase.
- **Email → temporal `events`.** Part of the deferred memory-feed phase.
- **Any non-readonly scope; send / draft / delete / label.** The credential is
  read-only by design.
- **A background email poller / sync.** This is on-demand lookup only; a poller
  is a later decision.
