import base64
import logging
import os
import re
from datetime import date

import httpx
import psycopg2
from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

POSTGRES_URL = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'athena')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'athena')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres.athena.svc.cluster.local')}:5432"
    f"/{os.getenv('POSTGRES_DB', 'athena')}"
)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama.athena.svc.cluster.local:11434")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng.athena.svc.cluster.local")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")

GITHUB_SOURCES = [
    "vanshb03/Summer2027-Internships",
    "SimplifyJobs/New-Grad-Positions",
]

GITHUB_API = "https://api.github.com"
RESUME_OPTIONS = {"AI & Agents", "Cloud & Infra", "Systems & Low-Level", "General SWE"}


# ---------------------------------------------------------------------------
# 2a — Data ingestion
# ---------------------------------------------------------------------------

def fetch_readme(repo: str) -> str:
    url = f"{GITHUB_API}/repos/{repo}/contents/README.md"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers={"Accept": "application/vnd.github.v3+json"})
        resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["content"]).decode("utf-8")


def _cell_text(cell: str) -> str:
    cell = cell.strip()
    cell = re.sub(r"\*+([^*]+)\*+", r"\1", cell)  # strip bold/italic
    return cell.strip()


def _extract_link(cell: str) -> str | None:
    m = re.search(r"\[([^\]]+)\]\((https?://[^)]+)\)", cell)
    if m:
        return m.group(2)
    m = re.search(r"(https?://\S+)", cell)
    if m:
        return m.group(1)
    return None


def _parse_table(lines: list[str]) -> list[dict]:
    if len(lines) < 2:
        return []

    def split_row(line: str) -> list[str]:
        return [c.strip() for c in line.strip("|").split("|")]

    header_cells = split_row(lines[0])

    col: dict[str, int] = {}
    for i, h in enumerate(header_cells):
        hl = h.lower()
        if "company" in hl:
            col["company"] = i
        elif "role" in hl or "position" in hl or "title" in hl:
            col["role"] = i
        elif "location" in hl:
            col["location"] = i
        elif "link" in hl or "apply" in hl or "application" in hl:
            col["apply_link"] = i

    if "company" not in col or "role" not in col:
        return []

    results = []
    for line in lines[1:]:
        cells = split_row(line)
        # Skip separator rows (---, :-:, etc.)
        if all(re.match(r"^[-:|]+$", c) for c in cells if c):
            continue

        n = len(cells)
        if col["company"] >= n or col["role"] >= n:
            continue

        company = _cell_text(cells[col["company"]])
        role = _cell_text(cells[col["role"]])

        if not company or not role or company.lower() == "company":
            continue

        if "location" in col and col["location"] < n:
            location = _cell_text(cells[col["location"]])
            location = re.sub(r"<br\s*/?>", ", ", location, flags=re.IGNORECASE)
            location = location.strip() or "Unknown"
        else:
            location = "Unknown"

        apply_link = None
        if "apply_link" in col and col["apply_link"] < n:
            raw = cells[col["apply_link"]]
            if "\U0001f512" in raw:  # 🔒 — closed posting
                continue
            apply_link = _extract_link(raw)

        results.append({"company": company, "role": role, "location": location, "apply_link": apply_link})

    return results


def parse_postings(markdown: str) -> list[dict]:
    postings: list[dict] = []
    table_lines: list[str] = []
    in_table = False

    for line in markdown.splitlines():
        if line.strip().startswith("|"):
            table_lines.append(line.strip())
            in_table = True
        else:
            if in_table and table_lines:
                postings.extend(_parse_table(table_lines))
                table_lines = []
            in_table = False

    if table_lines:
        postings.extend(_parse_table(table_lines))

    return postings


# ---------------------------------------------------------------------------
# 2b — Deduplication
# ---------------------------------------------------------------------------

def fetch_existing(conn) -> set[tuple]:
    with conn.cursor() as cur:
        cur.execute("SELECT company, role, location FROM internship_postings")
        return {(r[0], r[1], r[2]) for r in cur.fetchall()}


# ---------------------------------------------------------------------------
# 2c — Company research
# ---------------------------------------------------------------------------

def research_company(company: str) -> str:
    snippets: list[str] = []
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{SEARXNG_URL}/search",
                params={"q": f"{company} software engineering internship", "format": "json"},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])[:2]
            snippets = [r.get("content", "") for r in results if r.get("content")]
    except Exception as e:
        log.warning("SearXNG failed for %s: %s", company, e)

    if not snippets:
        return f"{company} is a technology company."

    prompt = (
        f"You are a research assistant. Based on these search snippets, write exactly one sentence "
        f"describing what {company} does and what their engineering team focuses on. "
        f"Be specific, not generic. Do not say 'I' or explain yourself. Just output the sentence.\n\n"
        f"Snippets:\n" + "\n".join(snippets)
    )

    try:
        with httpx.Client(timeout=90) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                      "options": {"num_ctx": 2048, "num_predict": 150}},
            )
            resp.raise_for_status()
            return resp.json()["response"].strip()
    except Exception as e:
        log.warning("Ollama company research failed for %s: %s", company, e)
        return f"{company} is a technology company."


# ---------------------------------------------------------------------------
# 2d — LLM scoring and resume routing
# ---------------------------------------------------------------------------

def score_posting(company: str, role: str, location: str, company_summary: str) -> tuple[int, str]:
    prompt = (
        "You are an internship advisor for a junior CS student (graduating Dec 2027) with experience in:\n"
        "- AI & Agents: Local LLMs, Python, RAG pipelines, LangGraph, Qdrant\n"
        "- Cloud & Infra: Kubernetes, Docker, Linux, self-hosted systems\n"
        "- Systems & Low-Level: Rust, C\n"
        "- General SWE: Full-stack, algorithms, data structures\n\n"
        "Rate this internship posting from 1-10 based on fit. Then pick the best resume.\n\n"
        f"Company: {company}\n"
        f"Role: {role}\n"
        f"Location: {location}\n"
        f"Company summary: {company_summary}\n\n"
        "Respond in exactly this format, nothing else:\n"
        "SCORE: <number>\n"
        "RESUME: <one of: AI & Agents | Cloud & Infra | Systems & Low-Level | General SWE>"
    )

    try:
        with httpx.Client(timeout=90) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                      "options": {"num_ctx": 2048, "num_predict": 150}},
            )
            resp.raise_for_status()
            text = resp.json()["response"].strip()

        score, resume = 5, "General SWE"
        for line in text.splitlines():
            if line.startswith("SCORE:"):
                try:
                    score = int(re.search(r"\d+", line.split(":", 1)[1]).group())
                    score = max(1, min(10, score))
                except (AttributeError, ValueError):
                    log.warning("Could not parse score from: %r", line)
            elif line.startswith("RESUME:"):
                candidate = line.split(":", 1)[1].strip()
                if candidate in RESUME_OPTIONS:
                    resume = candidate
                else:
                    log.warning("Unexpected resume value: %r", candidate)

        return score, resume
    except Exception as e:
        log.warning("Ollama scoring failed for %s/%s: %s", company, role, e)
        return 5, "General SWE"


# ---------------------------------------------------------------------------
# 2e — Write to Postgres
# ---------------------------------------------------------------------------

def insert_posting(conn, posting: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO internship_postings
                (company, role, location, apply_link, priority_score,
                 resume_recommendation, company_summary, status, found_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'new', CURRENT_DATE)
            ON CONFLICT (company, role, location) DO NOTHING
            """,
            (
                posting["company"],
                posting["role"],
                posting["location"],
                posting.get("apply_link"),
                posting["priority_score"],
                posting["resume_recommendation"],
                posting["company_summary"],
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# 2f — Daily report
# ---------------------------------------------------------------------------

def generate_report(conn) -> str:
    today = date.today()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT company, role, location, priority_score, resume_recommendation,
                   company_summary, apply_link, found_date
            FROM internship_postings
            WHERE found_date = %s
            ORDER BY priority_score DESC
            """,
            (today,),
        )
        rows = cur.fetchall()

    if not rows:
        return f"## CS Internship Morning Report: {today}\nNo new postings found today."

    high = [r for r in rows if r[3] >= 8]
    standard = [r for r in rows if r[3] < 8]

    def fmt(r) -> str:
        company, role, location, score, resume, summary, link, found_date = r
        apply_str = f"[Apply Here]({link})" if link else "No link"
        return (
            f"- **{company}** - {role} | {location} (Score: {score}/10)\n"
            f"  * **Use:** Resume — {resume}\n"
            f"  * **Context:** {summary}\n"
            f"  * **Posted:** {found_date} | {apply_str}"
        )

    lines = [
        f"## \U0001f305 CS Internship Morning Report: {today}",
        f"Found {len(rows)} new roles. {len(high)} high-priority matches.",
        "",
    ]
    if high:
        lines.append("### \U0001f6a8 High-Priority Matches (Score >= 8)")
        lines.extend(fmt(r) for r in high)
        lines.append("")
    if standard:
        lines.append("### \U0001f4cc Standard Matches (Score < 8)")
        lines.extend(fmt(r) for r in standard)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------

def run_pipeline() -> None:
    log.info("=== Internship Hunter pipeline starting ===")
    conn = psycopg2.connect(POSTGRES_URL)
    try:
        existing = fetch_existing(conn)
        log.info("Loaded %d existing postings from Postgres", len(existing))

        all_postings: list[dict] = []
        for repo in GITHUB_SOURCES:
            log.info("Fetching %s", repo)
            try:
                markdown = fetch_readme(repo)
                postings = parse_postings(markdown)
                log.info("Parsed %d postings from %s", len(postings), repo)
                all_postings.extend(postings)
            except Exception as e:
                log.error("Failed to process %s: %s", repo, e)

        new_postings = [
            p for p in all_postings
            if (p["company"], p["role"], p["location"]) not in existing
        ]
        log.info("%d new postings after deduplication", len(new_postings))

        for i, posting in enumerate(new_postings, 1):
            log.info("[%d/%d] %s — %s", i, len(new_postings), posting["company"], posting["role"])
            posting["company_summary"] = research_company(posting["company"])
            score, resume = score_posting(
                posting["company"], posting["role"],
                posting["location"], posting["company_summary"],
            )
            posting["priority_score"] = score
            posting["resume_recommendation"] = resume
            insert_posting(conn, posting)
            log.info("  score=%d resume=%s", score, resume)

        report = generate_report(conn)
        log.info("\n%s", report)
    finally:
        conn.close()

    log.info("=== Pipeline complete ===")


if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone="America/New_York")
    scheduler.add_job(run_pipeline, "cron", hour=6, minute=0)
    log.info("Scheduler started — pipeline fires daily at 06:00 ET")
    log.info("Running pipeline once on startup...")
    run_pipeline()
    scheduler.start()
