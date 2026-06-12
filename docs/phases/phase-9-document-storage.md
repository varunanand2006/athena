# Phase 9 — Document Storage & Catalog

## Goal
Upgrade the ingestion service from "embed and discard" into a full document management layer. Original files are retained on a persistent PVC, cataloged in Postgres, embedded into Qdrant with a stable `document_id`, and summarized once at ingestion. The agent gets catalog-browsing tools alongside the existing semantic `search_documents`, and the frontend gets a `/documents` view with upload, polling, and per-row delete.

## Phase gate
1. Upload a file via the frontend Documents view → row appears immediately with "Processing…", auto-refreshes to a real summary within ~1–5 min.
2. Drop a file directly into `/data/documents/` on vlinux2 (via `kubectl cp` or scp) → row appears in the catalog within 5 min, no frontend action.
3. In Chat, ask "what documents do you have access to" → agent calls `list_documents`. Ask "show me the table of contents" → agent calls `get_table_of_contents`. Ask "what's in my <doc>" → agent calls `get_document_summary`.
4. `_TABLE_OF_CONTENTS.md` exists on the PVC and lists every cataloged document.
5. Delete a document via the trash button → file is gone from the PVC, Qdrant chunks are filter-deleted, catalog row is removed; the watcher's next scan does **not** re-ingest it.
6. Re-upload an existing filename → only one catalog row remains for that filename, old chunks are gone (a follow-up `search_documents` query no longer returns the old content).

---

## Naming clarification
There are now two stores called `documents`. They hold different things:

- **Postgres table `documents`** — source of truth. One row per ingested file: id, filename, title, doc_type, file_path, summary, chunk_count, size_bytes, added_at. Used for catalog browsing, the TOC, and the agent's `list_documents` / `get_document_summary` tools.
- **Qdrant collection `documents`** — vector chunks. Each point's payload carries `text`, `filename`, and `document_id` (the catalog row's UUID). Used by the existing `search_documents` semantic search and for filter-delete on re-ingest/delete.

---

## What was built

### Persistent storage
**New PVC:** `cluster/ingestion/documents-pvc.yaml` — `ingestion-documents`, 10Gi, `local-path` storage class, ReadWriteOnce. Mounted at `/data/documents` in the ingestion pod.

**Node move:** `cluster/ingestion/deployment.yaml` switched from `nodeSelector: workload: ai` (xdev-sr) to `nodeSelector: kubernetes.io/hostname: vlinux2`. k3s `local-path` storage is node-local, so the pod must live on the node holding the PVC. Postgres env vars (`POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` from `postgres-secret`), `OLLAMA_MODEL`, and `INGESTION_DOCS_DIR` env vars were added.

### Postgres catalog
Appended to `scripts/migrate.sql`:

```sql
CREATE TABLE IF NOT EXISTS documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename    TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    doc_type    TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    summary     TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_added_at ON documents (added_at DESC);
```

`filename UNIQUE` is what makes re-ingest detectable.

### Ingestion service (`ingestion/main.py`)
**Two-phase ingest.** The original synchronous `_ingest_path` was split into:

- `_insert_catalog_row(file_path, original_filename) → document_id` — fast. On re-ingest cleanup (lookup by filename, qdrant filter-delete on `document_id`, catalog row DELETE), then INSERT a fresh row and return its UUID.
- `_embed_and_summarize(document_id, file_path, original_filename)` — heavy. Chunk via `SentenceSplitter(512, 64)`, embed each chunk with `nomic-embed-text`, upsert into Qdrant with payload `{text, filename, document_id}`, generate a 2–3 sentence summary by sending the first 2000 chars to gemma4:e2b via `/api/chat` with `think:false, num_ctx:2048, num_predict:150`, then `UPDATE documents SET chunk_count, summary` and regenerate the TOC. Logs and recovers on per-step failure rather than raising.

`POST /ingest` writes the upload to the PVC, calls `_insert_catalog_row`, spawns `threading.Thread(target=_embed_and_summarize, daemon=True).start()`, and returns immediately. This avoids the nginx/axios 180s proxy timeout for large files — the response comes back in well under a second.

`DELETE /ingest/documents/{document_id}` — three-way cleanup:
1. Qdrant `delete(points_selector=FilterSelector(filter=...document_id...))`
2. `DELETE FROM documents WHERE id = ...`
3. `Path(file_path).unlink(missing_ok=True)` — required because the watcher would otherwise re-ingest the file on its next scan
4. `_regenerate_toc()`

`GET /toc` — returns `/data/documents/_TABLE_OF_CONTENTS.md` as plain text. Regenerates it lazily if missing.

### Table of contents
`_regenerate_toc()` queries `SELECT title, doc_type, added_at, summary FROM documents ORDER BY added_at DESC`, formats a markdown table with header + count + rows, and writes atomically: `_TABLE_OF_CONTENTS.md.tmp` then `os.replace()`. Atomic write is required because the folder watcher reads the directory and must never see a half-written file. Called at the end of every ingest and every delete.

### Folder watcher
A FastAPI `lifespan` context manager replaces `@app.on_event("startup")`. Inside lifespan:
- `_ensure_collection()` and `DOCS_DIR.mkdir(...)` as before.
- `BackgroundScheduler(timezone="UTC")` with one job: `_scan_documents_folder` on a 5-minute interval (configurable via `INGESTION_WATCH_INTERVAL_MINUTES`), `next_run_time=datetime.now()` so it fires immediately on boot, `max_instances=1` + `coalesce=True` to prevent overlap.
- On shutdown: `scheduler.shutdown(wait=False)`.

`_scan_documents_folder()` queries the catalog's set of known filenames, walks `/data/documents`, skips the TOC file, anything starting with `_`, and `.tmp` partials, and for any unknown filename calls `_insert_catalog_row` + `_embed_and_summarize` inline (no thread — it's already in the scheduler's worker thread). Per-file try/except so one bad file doesn't stall the scan.

### Agent (`agent/main.py`)
New `INGESTION_URL` env var (`http://ingestion.athena.svc.cluster.local`).

Three new `@tool`s, registered alongside `web_search`, `search_documents`, `lookup_leetcode` in `create_react_agent`:

- `list_documents()` — `SELECT title, doc_type, added_at, summary FROM documents ORDER BY added_at DESC`, returns a newline digest with summary truncated to ~200 chars.
- `get_table_of_contents()` — `httpx GET {INGESTION_URL}/toc`, returns the markdown as-is.
- `get_document_summary(name: str)` — `WHERE filename ILIKE %name% OR title ILIKE %name% LIMIT 1`, returns `"{title}: {summary}"` or a not-found message.

`SYSTEM_PROMPT` distinguishes the new browsing tools from the existing semantic search.

New endpoint `GET /documents` returns the catalog as JSON for the frontend (id, filename, title, doc_type, summary, chunk_count, size_bytes, added_at).

### Frontend (`frontend/`)
**New view** `src/pages/DocumentsView.tsx`:
- Upload zone: labeled "Upload file" button (primary), with drag-drop on the surrounding area as a secondary path. Native HTML5 — no new npm dep. `<input type="file" multiple>` for multi-select.
- Sequential upload loop with per-file spinner in the upload status panel.
- Catalog table: Title (+ filename subtitle), Type, Added, Size, Summary, and a trash button column.
- Rows with `chunk_count === 0` render a spinner + "Processing…" in the summary column. A polling effect refetches `/documents` every 4s while any row is in that state, then stops.
- Delete: trash icon → `window.confirm` → `axios.delete('/ingest/documents/{id}')` → refetch.

**Wiring:**
- `App.tsx` — added `<Route path="/documents" element={<DocumentsView />} />`.
- `Sidebar.tsx` — added a Documents NavLink with an inline SVG icon.
- `nginx.conf` — extended the agent proxy regex to include `documents`, added a second `location ~ ^/(ingest|toc)` block proxying to ingestion. `client_max_body_size 20m`, `proxy_read_timeout 180s`, `proxy_request_buffering off` for the ingestion path.
- `vite.config.ts` — added `/documents`, `/ingest`, `/toc`, `/conversations` to the dev proxy.

---

## Issues encountered

### docker save | gzip hung on xdev-sr
`sudo docker save athena-ingestion:latest | gzip > /tmp/ingestion.tar.gz` hung for several minutes with no progress, blocking the Phase 1 deploy. xdev-sr is CPU-constrained and gzip is single-threaded on a multi-hundred-MB image. Fix: drop the gzip — `sudo docker save athena-ingestion:latest -o /tmp/ingestion.tar` completes in seconds. The slightly larger tarball (~no compression) transfers fine over LAN. Image-build lesson in CLAUDE.md was updated.

### ErrImageNeverPull on the new vlinux2 pod
Switching `nodeSelector` to `vlinux2` caused the new ReplicaSet pod to stay `Pending` with `ErrImageNeverPull` — the existing `athena-ingestion:latest` was only in xdev-sr's containerd, not vlinux2's. With `imagePullPolicy: Never` the pod can't recover on its own. One-time fix: save + scp + `k3s ctr images import` on vlinux2. Subsequent deploys ship straight to vlinux2.

### `kubectl cp` of migrate.sql ran the right SQL but the table didn't exist
Earlier in Step 2, `psql -f /tmp/migrate.sql` ran without errors yet `\d documents` returned "Did not find any relation". Root cause: the vlinux1 working tree hadn't pulled the Windows-side edit; the file copied into the pod was the pre-Phase-9 version, so the new CREATE TABLE was never executed. Fix: pull on vlinux1 first, or for one-off migrations skip the file copy entirely and run the SQL inline with `kubectl exec ... psql -c "..."` per the existing CLAUDE.md lesson.

### Heredoc indentation broke the inline migrate append
A `cat >> migrate.sql <<'EOF' ... EOF` paste captured leading whitespace from the terminal's continuation prefix, so the literal `EOF` token in the file body had leading spaces. The shell never matched it as the heredoc terminator, instead appending the entire pasted block — including `EOF` and the next `tail -20` line — into the file. Clean recovery: `git checkout HEAD -- scripts/migrate.sql`, then push the (correct) Windows version through git instead of re-running heredocs interactively. Inline `printf` or going through git is more reliable than terminal heredocs for content that already lives in version control.

### Large markdown file timed out the frontend
A larger .md upload returned "Failed to ingest" in the frontend even though the server completed successfully a couple of minutes later — the row appeared on the next page load. The synchronous `POST /ingest` exceeded both the nginx `proxy_read_timeout` and the axios upload timeout. Fix: the async refactor described above. `POST /ingest` now writes the catalog row and returns within ~1 second, and the heavy work runs in a daemon thread. Frontend polls until the row fills in. This is the right architecture regardless of file size — the watcher does the same work asynchronously and we now mirror that for direct uploads.

---

## Build process

```bash
# On xdev-sr — build images (docker is here)
ssh ubuntu@192.168.96.201
cd ~/athena && git pull
sudo docker build -t athena-ingestion:latest ingestion/
sudo docker build -t athena-agent:latest agent/
sudo docker build -t athena-frontend:latest frontend/
sudo docker save athena-ingestion:latest -o /tmp/ingestion.tar
sudo docker save athena-agent:latest      -o /tmp/agent.tar
sudo docker save athena-frontend:latest   -o /tmp/frontend.tar
sudo chmod 644 /tmp/*.tar

# Agent runs on xdev-sr — import locally
sudo k3s ctr images import /tmp/agent.tar

# Ingestion + frontend run on vlinux2 — ship over
ssh ubuntu@192.168.96.202
scp ubuntu@192.168.96.201:/tmp/ingestion.tar ubuntu@192.168.96.201:/tmp/frontend.tar /tmp/
sudo k3s ctr images import /tmp/ingestion.tar
sudo k3s ctr images import /tmp/frontend.tar

# From vlinux1 — apply manifests and restart
kubectl apply -f cluster/ingestion/documents-pvc.yaml
kubectl apply -f cluster/ingestion/deployment.yaml
kubectl apply -f cluster/agent/deployment.yaml
kubectl rollout restart deploy/ingestion deploy/agent deploy/frontend -n athena
```

---

## Next phase
TBD — to be decided in a Claude.ai planning chat. Candidates: Phase 4 (Rust MCP server), email ingestion to the same `documents` pipeline, or the long-promised SMS/Twilio notifications.
