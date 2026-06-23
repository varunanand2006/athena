import logging
import os
import sys
import time
from datetime import datetime, timezone

import httpx
import psycopg2
from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("leetcode")

LEETCODE_USERNAME = os.environ["LEETCODE_USERNAME"]
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama.athena.svc.cluster.local:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")

# Backend toggle (see agent/main.py). "openai" (default) sends problem analysis
# to gpt-4o-mini; "ollama" restores the original gemma4:e2b path. Gemma was
# retired for being too slow on CPU. Flip the env (no rebuild) to revert.
LLM_BACKEND = os.getenv("LLM_BACKEND", "openai")        # openai | ollama
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
POSTGRES_USER = os.getenv("POSTGRES_USER", "athena")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "athena")
POSTGRES_DB = os.getenv("POSTGRES_DB", "athena")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres.athena.svc.cluster.local")

PG_DSN = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:5432/{POSTGRES_DB}"
)

GRAPHQL_URL = "https://leetcode.com/graphql"
GRAPHQL_HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://leetcode.com",
}

RECENT_AC_QUERY = """
query recentAcSubmissions($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    id
    title
    titleSlug
    timestamp
  }
}
"""

QUESTION_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    difficulty
    topicTags { name }
  }
}
"""

# Bulk problemset listing — returns EVERY problem with its topic tags, paginated
# via limit/skip. Used by the backfill so we fetch the whole problemset's tags in
# a handful of calls and map them onto our solved problems, rather than one
# per-problem call.
ALL_QUESTIONS_QUERY = """
query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
  problemsetQuestionList: questionList(
    categorySlug: $categorySlug
    limit: $limit
    skip: $skip
    filters: $filters
  ) {
    total: totalNum
    questions: data {
      titleSlug
      topicTags { name }
    }
  }
}
"""


def _db():
    return psycopg2.connect(PG_DSN)


def _chat(prompt: str, max_tokens: int = 200) -> str:
    """Single-turn chat completion via the active backend (gpt-4o-mini by
    default, gemma4:e2b when LLM_BACKEND=ollama). Returns the message text, or
    "" if the model returned nothing. Raises on transport errors so the caller
    can skip/retry."""
    with httpx.Client(timeout=120) as client:
        if LLM_BACKEND == "ollama":
            resp = client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "think": False,
                    "stream": False,
                    "options": {"num_ctx": 2048, "num_predict": max_tokens},
                },
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "").strip()
        resp = client.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": OPENAI_CHAT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def _fetch_recent_accepted(limit: int = 20) -> list[dict]:
    with httpx.Client(timeout=20, headers=GRAPHQL_HEADERS) as client:
        resp = client.post(
            GRAPHQL_URL,
            json={
                "query": RECENT_AC_QUERY,
                "variables": {"username": LEETCODE_USERNAME, "limit": limit},
            },
        )
        resp.raise_for_status()
    return resp.json().get("data", {}).get("recentAcSubmissionList") or []


def _fetch_question_meta(slug: str) -> tuple[str, list[str]]:
    """Fetch a problem's difficulty and topic tags from LeetCode's GraphQL API.
    Topic tags are first-class metadata (e.g. "Dynamic Programming", "Hash
    Table") — no LLM needed. Returns (difficulty, topics)."""
    with httpx.Client(timeout=20, headers=GRAPHQL_HEADERS) as client:
        resp = client.post(
            GRAPHQL_URL,
            json={"query": QUESTION_QUERY, "variables": {"titleSlug": slug}},
        )
        resp.raise_for_status()
    question = resp.json().get("data", {}).get("question") or {}
    difficulty = question.get("difficulty", "Unknown")
    topics = [t["name"] for t in (question.get("topicTags") or []) if t.get("name")]
    return difficulty, topics


def _fetch_all_topic_tags(page_size: int = 100) -> dict[str, list[str]]:
    """Fetch every problem's topic tags from LeetCode's problemset list in bulk.

    Returns a {titleSlug: [topics]} map covering the WHOLE LeetCode problemset.
    LeetCode caps the list at 100 rows per request, so this pages through the full
    set (~40 calls) — still far fewer than one call per problem, and it covers
    everything in a single sweep. The loop advances `skip` by however many rows
    actually came back, so it self-corrects to whatever cap LeetCode enforces.
    """
    mapping: dict[str, list[str]] = {}
    skip = 0
    with httpx.Client(timeout=60, headers=GRAPHQL_HEADERS) as client:
        while True:
            resp = client.post(
                GRAPHQL_URL,
                json={
                    "query": ALL_QUESTIONS_QUERY,
                    "variables": {"categorySlug": "", "skip": skip, "limit": page_size, "filters": {}},
                },
            )
            resp.raise_for_status()
            block = resp.json().get("data", {}).get("problemsetQuestionList") or {}
            questions = block.get("questions") or []
            total = block.get("total") or 0
            for q in questions:
                slug = q.get("titleSlug")
                if slug:
                    mapping[slug] = [t["name"] for t in (q.get("topicTags") or []) if t.get("name")]
            skip += len(questions)
            if not questions or skip >= total:
                break
            time.sleep(0.3)  # be polite across the ~40 pages
    return mapping


def backfill_all_topics() -> None:
    """One-shot: populate `topics` for every already-stored problem.

    Fetches the whole problemset's tags in bulk (a few GraphQL calls), then maps
    them onto the slugs we already have in leetcode_problems. Idempotent — safe to
    re-run; it just overwrites topics with the current authoritative tags. Run via:
        kubectl exec -n athena deploy/leetcode -- python main.py backfill
    """
    log.info("Backfill: fetching all problem topic tags from LeetCode…")
    try:
        tag_map = _fetch_all_topic_tags()
    except Exception as exc:
        log.error("Backfill: failed to fetch problemset topics: %s", exc)
        return
    log.info("Backfill: fetched topics for %d problems from LeetCode", len(tag_map))

    conn = _db()
    updated, missing = 0, 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT slug FROM leetcode_problems")
            slugs = [r[0] for r in cur.fetchall()]

        for slug in slugs:
            topics = tag_map.get(slug)
            if topics is None:
                missing += 1
                continue
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE leetcode_problems SET topics = %s WHERE slug = %s",
                        (topics, slug),
                    )
            updated += 1
    finally:
        conn.close()

    log.info("Backfill done — %d problems updated, %d not found in LeetCode list",
             updated, missing)


def _should_queue(conn, slug: str, submitted_at: datetime) -> bool:
    """Queue only if never analyzed, or submission is newer than last analysis."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(analyzed_at) FROM leetcode_analysis WHERE problem_slug = %s",
            (slug,),
        )
        last_analyzed = cur.fetchone()[0]
    return last_analyzed is None or submitted_at > last_analyzed


def poll_job() -> None:
    log.info("Poll job starting for user: %s", LEETCODE_USERNAME)
    try:
        submissions = _fetch_recent_accepted()
    except Exception as exc:
        log.error("Failed to fetch submissions from LeetCode: %s", exc)
        return

    if not submissions:
        log.info("No recent accepted submissions returned")
        return

    conn = _db()
    queued = 0
    try:
        for sub in submissions:
            slug = sub["titleSlug"]
            title = sub["title"]
            lc_id = int(sub["id"])
            submitted_at = datetime.fromtimestamp(int(sub["timestamp"]), tz=timezone.utc)

            try:
                difficulty, topics = _fetch_question_meta(slug)
            except Exception as exc:
                log.warning("Could not fetch metadata for %s: %s", slug, exc)
                difficulty, topics = "Unknown", []

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO leetcode_problems (title, slug, difficulty, topics, solved_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (slug) DO UPDATE
                          SET solved_at   = EXCLUDED.solved_at,
                              difficulty  = EXCLUDED.difficulty,
                              topics      = EXCLUDED.topics
                        """,
                        (title, slug, difficulty, topics, submitted_at),
                    )
                    cur.execute(
                        """
                        INSERT INTO leetcode_submissions (id, problem_slug, difficulty, submitted_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (lc_id, slug, difficulty, submitted_at),
                    )

            if not _should_queue(conn, slug, submitted_at):
                continue

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO leetcode_queue (problem_slug, submitted_at, queued_at)
                        VALUES (%s, %s, now())
                        ON CONFLICT (problem_slug) DO UPDATE
                          SET submitted_at = EXCLUDED.submitted_at,
                              queued_at    = now()
                        """,
                        (slug, submitted_at),
                    )

            queued += 1
            log.info("Queued: %s (%s)", title, difficulty)
    finally:
        conn.close()

    log.info("Poll job done — %d/%d queued for analysis", queued, len(submissions))


def process_job() -> None:
    log.info("Process job starting")
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT problem_slug FROM leetcode_queue")
            slugs = [row[0] for row in cur.fetchall()]

        if not slugs:
            log.info("Queue is empty")
            return

        for slug in slugs:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT title, difficulty FROM leetcode_problems WHERE slug = %s",
                    (slug,),
                )
                row = cur.fetchone()

            if not row:
                log.warning("No problem record for slug %s, skipping", slug)
                continue

            title, difficulty = row
            prompt = (
                f"I solved the LeetCode problem '{title}' (difficulty: {difficulty}). "
                "In 2-3 sentences: identify the core algorithm or data structure pattern "
                "this problem uses, and suggest one specific thing to focus on to master this problem type."
            )

            try:
                analysis = _chat(prompt, max_tokens=200)
            except Exception as exc:
                log.error("LLM request failed for %s: %s", slug, exc)
                continue

            if not analysis:
                # Empty model output (notably the gemma thinking-model failure
                # mode): leave the item queued so the next run retries instead of
                # storing a blank analysis and dropping it from the queue.
                log.warning("Empty analysis for %s — leaving queued for retry", slug)
                continue

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO leetcode_analysis (problem_slug, analysis_text, analyzed_at)
                        VALUES (%s, %s, now())
                        """,
                        (slug, analysis),
                    )
                    cur.execute(
                        "DELETE FROM leetcode_queue WHERE problem_slug = %s",
                        (slug,),
                    )

            log.info("Analyzed and cleared: %s", slug)
    finally:
        conn.close()

    log.info("Process job done")


if __name__ == "__main__":
    # One-shot backfill mode: `python main.py backfill` populates topics for all
    # existing problems and exits (no scheduler). Triggered via kubectl exec.
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        backfill_all_topics()
        sys.exit(0)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(poll_job, "interval", hours=6, next_run_time=datetime.now(timezone.utc))
    # LLM analysis (process_job) is DISABLED — we only populate factual problem
    # data: difficulty + topic tags straight from LeetCode's API, no paid LLM
    # calls. The process_job/_chat code (and the queue poll_job fills) are kept
    # intact; re-add the line below to turn analysis back on.
    # scheduler.add_job(process_job, "cron", hour=23, minute=0)
    log.info("Scheduler started — user=%s (LLM analysis OFF; polling problems + topics only)",
             LEETCODE_USERNAME)
    scheduler.start()
