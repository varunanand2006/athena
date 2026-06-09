import logging
import os
from datetime import datetime, timezone

import httpx
import psycopg2
from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("leetcode")

LEETCODE_USERNAME = os.environ["LEETCODE_USERNAME"]
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama.athena.svc.cluster.local:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")
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
  }
}
"""


def _db():
    return psycopg2.connect(PG_DSN)


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


def _fetch_difficulty(slug: str) -> str:
    with httpx.Client(timeout=20, headers=GRAPHQL_HEADERS) as client:
        resp = client.post(
            GRAPHQL_URL,
            json={"query": QUESTION_QUERY, "variables": {"titleSlug": slug}},
        )
        resp.raise_for_status()
    return resp.json().get("data", {}).get("question", {}).get("difficulty", "Unknown")


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
    try:
        for sub in submissions:
            slug = sub["titleSlug"]
            title = sub["title"]
            lc_id = int(sub["id"])
            submitted_at = datetime.fromtimestamp(int(sub["timestamp"]), tz=timezone.utc)

            try:
                difficulty = _fetch_difficulty(slug)
            except Exception as exc:
                log.warning("Could not fetch difficulty for %s: %s", slug, exc)
                difficulty = "Unknown"

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO leetcode_problems (title, slug, difficulty, solved_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (slug) DO UPDATE
                          SET solved_at   = EXCLUDED.solved_at,
                              difficulty  = EXCLUDED.difficulty
                        """,
                        (title, slug, difficulty, submitted_at),
                    )
                    cur.execute(
                        """
                        INSERT INTO leetcode_submissions (id, problem_slug, difficulty, submitted_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (lc_id, slug, difficulty, submitted_at),
                    )
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

            log.info("Queued: %s (%s)", title, difficulty)
    finally:
        conn.close()

    log.info("Poll job done, processed %d submissions", len(submissions))


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
                with httpx.Client(timeout=120) as client:
                    resp = client.post(
                        f"{OLLAMA_BASE_URL}/api/generate",
                        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                    )
                    resp.raise_for_status()
                analysis = resp.json().get("response", "").strip()
            except Exception as exc:
                log.error("Ollama request failed for %s: %s", slug, exc)
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
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(poll_job, "interval", hours=6, next_run_time=datetime.now(timezone.utc))
    scheduler.add_job(process_job, "cron", hour=23, minute=0)
    log.info("Scheduler started — user=%s model=%s", LEETCODE_USERNAME, OLLAMA_MODEL)
    scheduler.start()
