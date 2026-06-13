# Phase 10 — Ingestion Reliability + System Health

## Goal
Two fixes to Phase 9 weak spots:

1. **Stuck-"Processing…" rows.** The Documents UI was inferring state from `chunk_count == 0`. If the background thread crashed mid-ingest (OOM, Ollama timeout, pod restart), the row sat forever and the UI polled it forever. Add a real `status` column with three explicit states, wire transitions through `_embed_and_summarize`, and add a reaper for pod-restart orphans.
2. **No app-level health view.** `kubectl` can tell you whether pods are running, but nothing inside the app told you whether the agent could actually reach its dependencies or how the data layer was trending. Add a `/system/health` aggregator on the agent and a `/system` view in the frontend.

## Phase gate
1. Upload a document → row goes `processing` → `complete`. Polling stops once settled.
2. Simulate an Ollama failure (point `OLLAMA_BASE_URL` at a bad host) → row ends `failed` (red badge), not stuck at `processing`. Revert.
3. Insert a fake `status='processing'` row with `added_at = now() - interval '40 minutes'`. Within ≤10 min the reaper flips it to `failed` and logs the reap. Delete the test row.
4. Open `/system` — all 5 services show green dots + sub-2 s latencies, data counts match `SELECT COUNT(*)`, view re-renders every 15 s. Scale ingestion to 0 → its dot turns red within one refresh. Scale back.

---

## What was built

### Schema
Appended to `scripts/migrate.sql`:

```sql
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'processing';

UPDATE documents SET status = 'complete'
 WHERE chunk_count > 0 AND status = 'processing';
```

`ADD COLUMN IF NOT EXISTS` is idempotent so the migration is safe to re-run. The backfill ensures existing rows with successful Phase 9 ingests (`chunk_count > 0`) start out as `complete` rather than the new column's default `'processing'`. The CREATE TABLE block in the same file was also updated to include `status TEXT NOT NULL DEFAULT 'processing'` so fresh installs pick it up directly.

### Ingestion service (`ingestion/main.py`)

**Helpers next to `pg_conn`:**

- `_mark_failed(document_id, reason)` — `UPDATE documents SET status='failed'` and `log.error` the reason. Wrapped in its own try/except so a DB outage during the failure path doesn't cascade.
- `_mark_complete(document_id)` — `UPDATE documents SET status='complete'`.

**`_embed_and_summarize` rewrite:**

- Wrapped in an outer `try: ... except Exception as e: _mark_failed(document_id, f"unexpected error: {e}")` for crashes the per-step blocks don't cover (e.g. `SentenceSplitter` blowing up).
- Each existing early-return failure site (text extraction, empty content, embedding, qdrant upsert, catalog UPDATE) gained an explicit `_mark_failed(document_id, "<reason>")` call before its `return`.
- `_mark_complete(document_id)` runs after the chunks-and-summary UPDATE succeeds, **before** `_regenerate_toc()` — the TOC is cosmetic, so a TOC-regen failure must not roll the row back to "failed".
- Summary-generation failure remains a partial success (chunks embedded, summary left empty) and does NOT mark the row failed.

**Reaper job in `lifespan`:**

```python
def _reap_stuck_documents() -> None:
    try:
        conn = pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE documents SET status='failed' "
                    "WHERE status='processing' "
                    "AND added_at < now() - interval '30 minutes' "
                    "RETURNING id, filename"
                )
                reaped = cur.fetchall()
                conn.commit()
            for doc_id, filename in reaped:
                log.warning("reaped stuck document id=%s filename=%s", doc_id, filename)
        finally:
            conn.close()
    except Exception:
        log.exception("reaper job failed")

scheduler.add_job(_reap_stuck_documents, "interval", minutes=10,
                  max_instances=1, coalesce=True, id="reap_stuck_documents")
```

Runs alongside the existing folder watcher in the same `BackgroundScheduler`. 30 min was chosen to comfortably exceed worst-case CPU-bound embedding+summary times even on gemma4:e2b.

### Agent (`agent/main.py`)

**`GET /documents`** gains `status` in the SELECT and the response dict. No CASE-WHEN derivation — it reads the real column.

**`GET /system/health`** is new. It reuses the existing env-configured service URLs (`INGESTION_URL`, `OLLAMA_BASE_URL`, `QDRANT_URL`, `SEARXNG_BASE_URL`) rather than hardcoding endpoints in a second place.

```python
SYSTEM_HEALTH_CHECKS = [
    ("ingestion", f"{INGESTION_URL}/healthz"),
    ("ollama",    f"{OLLAMA_BASE_URL}/api/tags"),
    ("qdrant",    f"{QDRANT_URL}/healthz"),
    ("searxng",   f"{SEARXNG_BASE_URL}/healthz"),
]
```

Each check is a 2 s `httpx.AsyncClient.get`. Any status `< 500` counts as reachable (a 404 on `/healthz` still proves the service is up enough to answer TCP). The 4 checks run concurrently via `asyncio.gather` so one slow dep can't stall the whole view. Agent self-check is hardcoded `reachable=true, latency_ms=0` — if `/system/health` responds at all, the agent is up.

After the pings the same handler does one Postgres round-trip:

```python
SELECT status, COUNT(*) FROM documents GROUP BY status;
SELECT COUNT(*) FROM documents;
SELECT COUNT(*) FROM internship_postings;
SELECT MAX(found_date) FROM internship_postings;
SELECT COUNT(*) FROM leetcode_problems;
SELECT MAX(solved_at) FROM leetcode_problems;
```

Returns one JSON with `services[]` and `data{}` so the frontend can render the whole view from a single response.

### Frontend

**`DocumentsView.tsx`:**

- `Document` interface gains `status: 'processing' | 'complete' | 'failed'`.
- Polling condition is `docs.some(d => d.status === 'processing')` — refetch stops as soon as every row settles into `complete` or `failed`.
- Summary cell renders three states:
  - `processing`: existing spinner + "Processing…"
  - `failed`: red `Failed` badge + "Delete and re-upload to retry" hint
  - `complete`: the summary text (existing behavior)
- The delete button's `title` switches between "Delete document" and "Delete and re-upload to retry" depending on row state. The button itself is unchanged — per the locked decision, retry is just delete (no auto-picker).

**`SystemView.tsx`** (new):

- Polls `GET /system/health` on mount and every 15 s via `setInterval`.
- **Services section**: one card with a row per service. Green dot (#4ADE80) if reachable, red dot (#F87171) if not. Latency rendered as `{n} ms` or `unreachable`.
- **Data section**: three cards in a `md:grid-cols-3`.
  - Documents — total + `complete: X · processing: Y · failed: Z` sub-line (failed in red when > 0).
  - Internships found — total + "last poll: …" relative time.
  - LeetCode solved — total + "last solved: …" relative time.
- Uses plain axios. No new npm dep.

**`utils/time.ts`** (new) — `relativeTime(iso)` extracted from Sidebar so both views share one implementation.

**Wiring:**

- `App.tsx` — `<Route path="/system" element={<SystemView />} />`.
- `Sidebar.tsx` — new System NavLink + `IconSystem`, plus the `relativeTime` import switched to the shared util.
- `nginx.conf` — extended the agent proxy regex from `^/(chat|conversations|internships|leetcode|healthz|documents)` to include `system`. No new `location` block needed.

---

## Issues encountered

### Heredoc-leftover migrate.sql (recurrence of Phase 9 issue)
The vlinux1 working tree of `scripts/migrate.sql` still had a 135-line garbled version from a Phase 9 paste, with a duplicate documents block and a stray literal `EOF` line at the bottom. Running `psql -f /tmp/migrate.sql` produced a `syntax error at or near "EOF"` and the new `ALTER TABLE` lines that came after the junk never executed — `\d documents` showed no `status` column. Recovery: `git checkout -- scripts/migrate.sql` on vlinux1 to drop the local junk, `git pull` to bring in the clean Windows version, then re-run `kubectl cp` + `psql -f`. Reinforces the Phase 9 lesson: for migrations, push from Windows through git rather than touching files on vlinux1.

### Terminal line-wrapping broke embedded Python
`kubectl exec -- python -c "..."` calls kept failing with `IndentationError: unexpected indent` because the user's terminal wrapped the long single-line command across two lines, indenting the continuation. Working around it by trying shorter one-liners didn't help — the wrap recurred. Fix: skip embedded Python entirely and use `kubectl port-forward` from vlinux1, then plain `curl` to hit the endpoint. Worth remembering for any future debugging that needs to exec into a pod with a multi-token command.

---

## Build process

```bash
# On xdev-sr — build images
ssh ubuntu@192.168.96.201
cd ~/athena && git pull
sudo docker build -t athena-ingestion:phase10 -f ingestion/Dockerfile ingestion/
sudo docker build -t athena-agent:phase10     -f agent/Dockerfile     agent/
sudo docker build -t athena-frontend:phase10  -f frontend/Dockerfile  frontend/
sudo docker save -o /tmp/athena-ingestion.tar athena-ingestion:phase10
sudo docker save -o /tmp/athena-agent.tar     athena-agent:phase10
sudo docker save -o /tmp/athena-frontend.tar  athena-frontend:phase10
sudo chmod 644 /tmp/athena-*.tar

# Agent runs on xdev-sr — import locally
sudo k3s ctr images import /tmp/athena-agent.tar

# Ingestion + frontend run on vlinux2 — ship over
ssh ubuntu@192.168.96.202
scp ubuntu@192.168.96.201:/tmp/athena-ingestion.tar /tmp/
scp ubuntu@192.168.96.201:/tmp/athena-frontend.tar  /tmp/
sudo k3s ctr images import /tmp/athena-ingestion.tar
sudo k3s ctr images import /tmp/athena-frontend.tar

# From vlinux1 — apply migration, then roll the three deployments
kubectl cp scripts/migrate.sql athena/<postgres-pod>:/tmp/migrate.sql
kubectl exec -n athena <postgres-pod> -- psql -U athena -d athena -f /tmp/migrate.sql
kubectl set image -n athena deploy/ingestion ingestion=athena-ingestion:phase10
kubectl set image -n athena deploy/agent     agent=athena-agent:phase10
kubectl set image -n athena deploy/frontend  frontend=athena-frontend:phase10
kubectl rollout status -n athena deploy/ingestion
kubectl rollout status -n athena deploy/agent
kubectl rollout status -n athena deploy/frontend
```

Migration **must** run before the agent rolls — the new agent SELECTs `status` from the documents table and will 500 against the un-migrated schema.

---

## Next phase
TBD — to be decided in a Claude.ai planning chat. Candidates carrying over from Phase 9: Phase 4 (Rust MCP server), email ingestion into the `documents` pipeline, Twilio SMS notifications, or expanding `/system/health` into per-service metrics (cache hit rate, embedding queue depth).
