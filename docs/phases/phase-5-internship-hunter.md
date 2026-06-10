# Phase 5 — Internship Hunter

## Goal
Daily automated pipeline that finds new CS internship postings, scores them against the user's profile, researches the companies, and stores results in Postgres. Morning report logged to stdout; surfaced in the frontend digest view.

## Phase gate
Trigger the pipeline manually inside the pod. Verify `internship_postings` has rows in Postgres with varied `priority_score` values and non-empty `company_summary`.

---

## What was built

### Postgres schema (`scripts/migrate.sql`)
`internship_postings` table:
- `UNIQUE (company, role, location)` — deduplication constraint
- `status` default `'new'` — tracks applied/ignored state
- `found_date` default `CURRENT_DATE` — used for daily report filtering

### Internship hunter service (`internship/`)
APScheduler `BlockingScheduler` running one job daily at 06:00 ET. Pipeline runs once on startup for immediate testability on fresh deploys.

Pipeline stages:
1. **Fetch** — GitHub REST API (`/repos/{owner}/{repo}/contents/README.md`), base64-decoded
2. **Parse** — Regex-based markdown table parser; skips `↳` continuation rows and `🔒` locked postings
3. **Deduplicate** — in-memory set from Postgres + `ON CONFLICT DO NOTHING` on insert
4. **Research** — SearXNG search (top 2 snippets) → Ollama one-sentence company summary
5. **Score** — Ollama rates fit 1-10 and picks resume track (AI & Agents / Cloud & Infra / Systems & Low-Level / General SWE)
6. **Insert** — writes to `internship_postings`
7. **Report** — queries today's postings, renders Markdown morning report to stdout

Sources:
- `vanshb03/Summer2027-Internships` — ~39 postings parsed
- `SimplifyJobs/New-Grad-Positions` — 0 parsed (different table format, TODO)

### k8s manifests (`cluster/internship/`)
- `deployment.yaml` — single replica, `nodeSelector: kubernetes.io/hostname: vlinux2`, env from `postgres-secret`
- `service.yaml` — ClusterIP only, no ingress needed

---

## Issues encountered

### gemma4:e2b is a thinking model
`/api/generate` returned empty `response` for all non-trivial prompts. Root cause: the model spends all `num_predict` tokens on internal reasoning (visible in the `thinking` field), leaving nothing for `content`. `done_reason: "length"` in the response was the tell.

Fix: switch to `/api/chat` with `"think": false`. Read from `message.content`.

### Image built from stale code
Edits made on varunlaptop weren't committed/pushed before building on xdev-sr. The build used the old `git pull` copy. Fix: always commit and push from varunlaptop before building on xdev-sr.

### `sudo docker save` creates root-owned tar
`scp` fails with "Permission denied" when the tar is owned by root. Fix: `sudo chmod 644 /tmp/<image>.tar` before scp.

### `kubectl exec` stdin unreliable for SQL
Piping a migration file via `< file` through `kubectl exec` ran silently without creating tables. Fix: `kubectl cp` the file into the pod, then `psql -f /tmp/migrate.sql`.

### Case-sensitive score parsing
`gemma4:e2b` outputs `Score:` not `SCORE:`. Parser defaulted to score=5 for all postings. Fix: `line.lower().startswith("score:")`.

---

## Build process
```bash
# On xdev-sr
sudo docker build -t athena-internship:latest ~/athena/internship/
sudo docker save athena-internship:latest -o /tmp/athena-internship.tar
sudo chmod 644 /tmp/athena-internship.tar
scp /tmp/athena-internship.tar varun@192.168.96.202:/tmp/

# On vlinux2
sudo k3s ctr images import /tmp/athena-internship.tar

# On vlinux1
kubectl apply -f ~/projects/athena/cluster/internship/
kubectl rollout restart deployment/internship-hunter -n athena
```

## Manual pipeline trigger (for testing)
```bash
kubectl exec -n athena deploy/internship-hunter -- python -c "from main import run_pipeline; run_pipeline()"
```

---

## Next phase
Phase 6 — React frontend at `athena.local` with chat UI and dashboard cards for internship digest and LeetCode stats.
