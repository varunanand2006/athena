# Phase 6 — LeetCode Poller & React Frontend

## Goal
Two parallel tracks: a LeetCode polling service that syncs accepted submissions to Postgres and queues them for Ollama analysis, and a React frontend that replaces raw `curl` access with a proper UI for chat, internship tracking, and LeetCode stats.

## Phase gate
- Visit `athena.local` in a browser; chat with the agent and get a response.
- Open the dashboard — internship cards and LeetCode stats load from the agent API.

---

## What was built

### LeetCode poller service (`leetcode/`)
APScheduler `BlockingScheduler` running daily. Pipeline runs once on startup.

Pipeline:
1. **Fetch submissions** — LeetCode GraphQL `recentAcSubmissionList` for user `varunanand2006`, limit 20
2. **Fetch difficulty** — separate GraphQL query per problem (`questionData`)
3. **Upsert to Postgres** — `leetcode_problems` (slug PK, title, difficulty, solved_at) and `leetcode_submissions`
4. **Queue analysis** — new or updated problems go into `leetcode_queue` for background Ollama analysis

Scheduled on vlinux2 via `nodeSelector: kubernetes.io/hostname: vlinux2`.

### Postgres schema additions (`scripts/migrate.sql`)
Four new tables added alongside the Phase 3 schema:
- `leetcode_problems` (slug PK, title, difficulty, solved_at)
- `leetcode_submissions` (id PK, problem_slug, difficulty, submitted_at)
- `leetcode_analysis` (problem_slug, analysis_text, analyzed_at)
- `leetcode_queue` (problem_slug PK, submitted_at, queued_at)

### Agent API additions (`agent/main.py`)
Two new REST endpoints:
- `GET /internships` — returns today's rows from `internship_postings` ordered by `priority_score DESC`
- `GET /leetcode` — returns difficulty breakdown (easy/medium/hard counts) and last solved date
- `lookup_leetcode` tool — joins `leetcode_problems` and `leetcode_analysis`, returns difficulty breakdown and 15 most recent problems with any available Ollama analysis

### React frontend (`frontend/`)
React 18, Vite 5, TypeScript, Tailwind CSS. Served by nginx on vlinux2 at `athena.local`. Nginx proxies `/chat`, `/internships`, `/leetcode`, `/healthz` to the agent ClusterIP.

Two views:
- **ChatView** — textarea input (Enter to send, Shift+Enter for newline), assistant messages rendered as Markdown via `react-markdown`, 120s axios timeout, animated loading dots
- **DashboardView** — three cards:
  - **Internship Pipeline** — today's postings with priority score badge (red ≥8, amber ≥5, grey <5), resume recommendation, apply link
  - **LeetCode Stats** — bar chart (recharts) for Easy/Medium/Hard, total count, last solved date
  - **Recent Activity** — last 10 user messages with timestamp

Sidebar health-checks the agent every 30s.

---

## Issues encountered

### `proxy_read_timeout` default too short
Nginx's default 60s read timeout caused 504 errors on slow LLM responses. Fixed by adding `proxy_read_timeout 120s` to `nginx.conf`.

### `sudo docker save` creates root-owned tar
`scp` fails with "Permission denied" when the tar is owned by root. Fix: `sudo chmod 644 /tmp/<image>.tar` before scp.

---

## Build process
```bash
# On xdev-sr (build frontend image)
sudo docker build -t athena-frontend:latest ~/athena/frontend/
sudo docker save athena-frontend:latest -o /tmp/athena-frontend.tar
sudo chmod 644 /tmp/athena-frontend.tar
scp /tmp/athena-frontend.tar varun@192.168.96.202:/tmp/

# On vlinux2
sudo k3s ctr images import /tmp/athena-frontend.tar

# On vlinux1
kubectl apply -f ~/projects/athena/cluster/frontend/
kubectl rollout restart deployment/frontend -n athena

# Same process for leetcode image (build on xdev-sr, deploy to vlinux2)
sudo docker build -t athena-leetcode:latest ~/athena/leetcode/
sudo docker save athena-leetcode:latest -o /tmp/athena-leetcode.tar
sudo chmod 644 /tmp/athena-leetcode.tar
scp /tmp/athena-leetcode.tar varun@192.168.96.202:/tmp/

# On vlinux2
sudo k3s ctr images import /tmp/athena-leetcode.tar
kubectl rollout restart deployment/leetcode-poller -n athena
```

---

## Next phase
Phase 7 — Model Router. Add OpenAI GPT-4o-mini as the chat LLM while keeping gemma4:e2b for background pipeline tasks.
