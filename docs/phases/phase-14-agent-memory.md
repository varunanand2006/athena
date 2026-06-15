# Phase 14 — Agent Memory (Substrate + Explicit Writes)

## Goal
Give Athena a persistent, human-viewable memory: a vault of markdown
notes the agent reads and writes. This phase is **explicit capture
only** — the agent writes a memory when the user says to ("remember
that…"), never on its own. Retrieval is **title/tag string matching, no
embeddings**. The vault is a folder of discrete markdown notes (one file
per topic), openable in Obsidian.

Two things deliberately deferred:
- **Automatic capture** → Phase 15 (a capture-*policy* problem, planned
  in chat before building).
- **Embedding-based retrieval** → later, additive to the note format.

See [ADR 007](../adr/007-agent-memory-vault.md) for the rationale behind
file-vault-over-DB, many-small-notes, explicit-before-automatic, and
title-before-embeddings.

## Phase gate
1. In the frontend chat: *"remember that I'm prepping for a Meta
   interview and I'm weak on graph problems."* → a note appears in
   `/data/memory/` with proper frontmatter, openable in Obsidian.
2. Start a **new** conversation, ask *"what am I prepping for?"* → the
   agent calls `search_memory` and recalls the Meta note.
3. *"also remember I applied to Cloudflare on June 10"* → a **second,
   distinct** note is created (not merged into the first).
4. Both notes show in the frontend `/memory` view.

---

## What was built

### Step 1 — Memory vault storage (`cluster/agent/`)
- **`memory-pvc.yaml`** — a `local-path` PVC `agent-memory` (1Gi, RWO),
  mounted into the agent at `/data/memory`.
- **`deployment.yaml`** — added the `memory` volume + `volumeMount`, and
  bumped the image to `:phase14`.

**Node pinning — the thing to get right up front.** `local-path` binds
the PV to whichever node first schedules a mounting pod. The agent is
pinned to **xdev-sr** via `nodeSelector: workload: ai`, so the memory
PVC binds there. This is deliberately a **different node** from the
documents PVC (`ingestion-documents` on vlinux2): the agent and the
documents store are not co-located, so the memory PVC had to be created
on the agent's node, not alongside the documents PVC. Verified the PVC
bound and the agent pod stayed `Running` on xdev-sr before adding code.

### Step 2 — Note format (`agent/memory.py`)
Every memory is a UTF-8 markdown file with YAML frontmatter + body:

```markdown
---
title: Meta interview prep
created: 2026-06-15
updated: 2026-06-15
tags: [interview, meta]
---

Varun is preparing for a Meta software engineering interview...
```

Filenames are the slugified title (`meta-interview-prep.md`). **The slug
is the note's identity** — the hook the update-vs-duplicate logic hangs
on. `agent/memory.py` owns the format: the spec lives in the module
docstring (the canonical comment), plus `MEMORY_DIR` and `slugify()`.
Documented in CLAUDE.md.

### Step 3 — Memory tools (`agent/main.py` + `agent/memory.py`)
Three `@tool`s, registered in `create_react_agent`, backed by helpers in
`memory.py`:

| Tool | Behavior |
| ---- | -------- |
| `write_memory(title, content, tags)` | Create, or **update in place** if a note with the same slug exists (append a dated addition to the body, union tags, bump `updated`). Atomic write via `.tmp` + `os.replace`. |
| `list_memories()` | Frontmatter-only index (title, tags, updated), newest first — the agent's index for deciding what to load. |
| `search_memory(query)` | No-embedding retrieval: word-level keyword/tag/slug matching with light stemming; returns the full body of the best matches, or says nothing matched. |

Frontmatter parsing/serialization is hand-rolled (no PyYAML dep → no
pyproject/Dockerfile sync). Matching drops stopwords and single-char
terms (so "what am I prepping **for**" doesn't match every note) and uses
substring + shared-prefix stemming (so "prepping" matches a "prep" title
and "applied" matches "application").

**System prompt** updated: call `write_memory` **only** on an explicit
"remember/note/save" instruction (never autonomously this phase); call
`search_memory`/`list_memories` before answering recall questions.

### Step 4 — Memory in the frontend
- **Agent:** `GET /memory` (frontmatter index) and `GET /memory/{slug}`
  (full note, 404 if missing).
- **Frontend:** `MemoryView.tsx` — master/detail: note list (title, tag
  chips, updated date) → click loads the note and renders its body with
  `ReactMarkdown`. Read-only; writing happens through chat. Route in
  `App.tsx`, nav link in `Sidebar.tsx`.
- **`nginx.conf`** — `memory` added to the agent proxy location regex.

### Step 5 — Docs
This phase doc, [ADR 007](../adr/007-agent-memory-vault.md), and the
CLAUDE.md updates (vault PVC + node pinning, note format, the three
tools, explicit-only capture, title-based retrieval, the `/memory` view,
current phase = 14).

---

## Issues encountered

### Frontend rollout hung for ~30 minutes — three stacked causes
The agent rolled out cleanly; the frontend did not. Peeling the onion:

1. **`ErrImageNeverPull`.** The new frontend pod couldn't find its image.
2. **Cluster/repo drift.** The live Deployment was pinned to
   `athena-frontend:phase10b`, while the repo said `:latest`. The old pod
   had run for days only because its container pinned `phase10b` *by
   digest*; the tag itself was gone, so every new pod requested a missing
   tag. Fixed by `kubectl apply`-ing the repo's `:latest` deployment.
3. **Wrong-directory build (the real root cause).** The "frontend" image
   was actually the **agent** — it ran `uvicorn` on `:8000`, so the
   `:80` readiness probe got connection refused. The build had been run
   from the wrong directory: **the repo is at `~/athena` on xdev-sr, but
   `~/projects/athena` on vlinux1.** `cd ~/projects/athena/frontend`
   silently failed on xdev-sr, leaving the shell in `~/athena/agent`, and
   `docker build -t athena-frontend:latest .` packaged the agent. The
   tell: the build finished *instantly from cache* (real frontend build
   runs `npm ci && npm run build`).

**Lessons recorded in memory:** per-machine repo paths
(`project-repo-paths-per-machine`), and always gate a build with
`docker image inspect <img> --format '{{json .Config.Cmd}}'` — the
frontend must show `["nginx","-g","daemon off;"]`, the agent shows
uvicorn.

---

## Next phase

**Phase 15 — automatic capture.** The substrate is ready: notes write
through the same `write_memory` path and format, so Phase 15 adds only a
*trigger policy* (when/what to capture, noise/PII avoidance) — to be
planned in chat before building. Embedding-based retrieval can be layered
on later without changing the note format.
