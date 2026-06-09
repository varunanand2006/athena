import logging
import os
from datetime import datetime, timezone

import httpx
import psycopg2
from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("leetcode")

LEETCODE_USERNAME = os.environ["LEETCODE_USERNAME"]
LEETCODE_SESSION = os.getenv("LEETCODE_SESSION", "")
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

USER_STATUS_QUERY = """
query globalData {
  userStatus {
    isSignedIn
    username
  }
}
"""

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
    topicTags {
      name
    }
  }
}
"""

SUBMISSION_LIST_QUERY = """
query submissionList($offset: Int!, $limit: Int!, $questionSlug: String) {
  submissionList(offset: $offset, limit: $limit, questionSlug: $questionSlug) {
    hasNext
    submissions {
      id
      statusDisplay
      timestamp
      title
      titleSlug
    }
  }
}
"""

SUBMISSION_DETAIL_QUERY = """
query submissionDetails($submissionId: Int!) {
  submissionDetails(submissionId: $submissionId) {
    code
    lang {
      name
    }
  }
}
"""

_csrf_token: str = ""


def _fetch_csrf() -> str:
    with httpx.Client(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = client.get("https://leetcode.com/")
        return resp.cookies.get("csrftoken", "nocsrf")


def _headers() -> dict:
    """Build GraphQL request headers, injecting session cookie when configured."""
    global _csrf_token
    base = {
        "Content-Type": "application/json",
        "Referer": "https://leetcode.com",
    }
    if not LEETCODE_SESSION:
        return base
    if not _csrf_token:
        try:
            _csrf_token = _fetch_csrf()
        except Exception as exc:
            log.warning("Could not fetch csrftoken: %s", exc)
            _csrf_token = "nocsrf"
    return {
        **base,
        "Cookie": f"LEETCODE_SESSION={LEETCODE_SESSION}; csrftoken={_csrf_token}",
        "x-csrftoken": _csrf_token,
    }


def _check_auth() -> bool:
    """Returns True if LEETCODE_SESSION is set and accepted by LeetCode."""
    if not LEETCODE_SESSION:
        return False
    try:
        with httpx.Client(timeout=10, headers=_headers()) as client:
            resp = client.post(GRAPHQL_URL, json={"query": USER_STATUS_QUERY})
            resp.raise_for_status()
        return bool(resp.json().get("data", {}).get("userStatus", {}).get("isSignedIn"))
    except Exception as exc:
        log.warning("Auth check request failed: %s", exc)
        return False


def _db():
    return psycopg2.connect(PG_DSN)


def _fetch_recent_accepted(limit: int = 20) -> list[dict]:
    with httpx.Client(timeout=20, headers=_headers()) as client:
        resp = client.post(
            GRAPHQL_URL,
            json={
                "query": RECENT_AC_QUERY,
                "variables": {"username": LEETCODE_USERNAME, "limit": limit},
            },
        )
        resp.raise_for_status()
    return resp.json().get("data", {}).get("recentAcSubmissionList") or []


def _fetch_all_accepted(max_submissions: int = 200) -> list[dict]:
    """Paginate submissionList and return only accepted submissions (requires auth)."""
    results: list[dict] = []
    limit = 20
    offset = 0
    while offset < max_submissions:
        with httpx.Client(timeout=20, headers=_headers()) as client:
            resp = client.post(
                GRAPHQL_URL,
                json={
                    "query": SUBMISSION_LIST_QUERY,
                    "variables": {"offset": offset, "limit": limit, "questionSlug": ""},
                },
            )
            resp.raise_for_status()
        data = resp.json().get("data", {}).get("submissionList", {}) or {}
        page = data.get("submissions") or []
        results.extend(s for s in page if s.get("statusDisplay") == "Accepted")
        if not data.get("hasNext"):
            break
        offset += limit
    return results


def _fetch_submission_code(submission_id: int) -> str | None:
    try:
        with httpx.Client(timeout=20, headers=_headers()) as client:
            resp = client.post(
                GRAPHQL_URL,
                json={
                    "query": SUBMISSION_DETAIL_QUERY,
                    "variables": {"submissionId": submission_id},
                },
            )
            resp.raise_for_status()
        return resp.json().get("data", {}).get("submissionDetails", {}).get("code")
    except Exception as exc:
        log.warning("Could not fetch code for submission %d: %s", submission_id, exc)
        return None


def _fetch_question_meta(slug: str) -> tuple[str, list[str]]:
    """Returns (difficulty, [topic, ...])."""
    with httpx.Client(timeout=20, headers=_headers()) as client:
        resp = client.post(
            GRAPHQL_URL,
            json={"query": QUESTION_QUERY, "variables": {"titleSlug": slug}},
        )
        resp.raise_for_status()
    q = resp.json().get("data", {}).get("question", {}) or {}
    difficulty = q.get("difficulty", "Unknown")
    topics = [t["name"] for t in q.get("topicTags", []) if t.get("name")]
    return difficulty, topics


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

    authenticated = _check_auth()
    if LEETCODE_SESSION and not authenticated:
        log.warning("[WARN] LeetCode session expired — falling back to public data")

    try:
        if authenticated:
            submissions = _fetch_all_accepted()
            log.info("Fetched %d accepted submissions (authenticated)", len(submissions))
        else:
            submissions = _fetch_recent_accepted()
            log.info("Fetched %d recent accepted submissions (public)", len(submissions))
    except Exception as exc:
        log.error("Failed to fetch submissions: %s", exc)
        return

    if not submissions:
        log.info("No accepted submissions returned")
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
                log.warning("Could not fetch meta for %s: %s", slug, exc)
                difficulty, topics = "Unknown", []

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO leetcode_problems (title, slug, difficulty, topics, solved_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (slug) DO UPDATE
                          SET solved_at  = EXCLUDED.solved_at,
                              difficulty = EXCLUDED.difficulty,
                              topics     = EXCLUDED.topics
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

            code = _fetch_submission_code(lc_id) if authenticated else None

            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO leetcode_queue
                          (problem_slug, submitted_at, queued_at, submission_code)
                        VALUES (%s, %s, now(), %s)
                        ON CONFLICT (problem_slug) DO UPDATE
                          SET submitted_at    = EXCLUDED.submitted_at,
                              queued_at       = now(),
                              submission_code = EXCLUDED.submission_code
                        """,
                        (slug, submitted_at, code),
                    )

            queued += 1
            log.info("Queued: %s (%s)%s", title, difficulty, " [+code]" if code else "")
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
