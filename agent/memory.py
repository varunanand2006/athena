"""Agent memory vault — note format + helpers (Phase 14).

The memory vault is a folder of discrete markdown notes (an Obsidian
vault) mounted into the agent at /data/memory. One file per memory /
topic. This module owns the on-disk FORMAT CONVENTION; the memory tools
(write_memory / list_memories / search_memory) in main.py build on it.

NOTE FORMAT
-----------
Every memory note is a UTF-8 markdown file with YAML frontmatter followed
by a free-text body:

    ---
    title: Meta interview prep
    created: 2026-06-15
    updated: 2026-06-15
    tags: [interview, meta]
    ---

    Varun is preparing for a Meta software engineering interview...

Rules:
  * `title`   — the human-readable note title (required).
  * `created` — ISO date (YYYY-MM-DD) the note was first written.
  * `updated` — ISO date of the most recent write; bumped on every update.
  * `tags`    — a YAML list; may be empty (`tags: []`).
  * The body is everything after the closing `---`, free-form markdown.

FILENAME CONVENTION
-------------------
The filename is the slugified title plus `.md`, e.g.
"Meta interview prep" -> `meta-interview-prep.md`. The slug is the note's
identity: a write whose title slugifies to an existing filename UPDATES
that note in place rather than creating a duplicate. This is what keeps
the vault from filling with near-duplicate notes on the same topic.

This format is Obsidian-compatible (open /data/memory as a vault) and
gives Phase 15's automatic capture a reliable structure to parse and
update. Phase 14 is EXPLICIT capture only — notes are written only when
the user says to remember something; retrieval is title/tag string
matching, no embeddings.
"""

import os
import re
from datetime import date

MEMORY_DIR = os.getenv("MEMORY_DIR", "/data/memory")


def slugify(title: str) -> str:
    """Convert a note title into its canonical filename slug.

    Lowercase, non-alphanumeric runs collapse to single hyphens, leading
    and trailing hyphens stripped. The slug is the note's stable identity
    for update-vs-create: same slug == same note.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"


# --- Frontmatter (de)serialization ----------------------------------------
# Hand-rolled rather than pulling in PyYAML: the format is fixed and tiny,
# and avoiding the dep means no pyproject/Dockerfile sync to maintain.


def _parse_tags(raw: str) -> list[str]:
    """Parse a `tags:` value. Supports flow style `[a, b]` (what we write)
    and a bare comma list `a, b`. Empty / `[]` -> []."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [t.strip() for t in raw.split(",") if t.strip()]


def _format_tags(tags: list[str]) -> str:
    return "[" + ", ".join(tags) + "]"


def parse_note(text: str) -> tuple[dict, str]:
    """Split a note's raw text into (frontmatter dict, body).

    Tolerant of missing frontmatter: if the file doesn't start with a
    `---` fence we treat the whole thing as the body with empty metadata.
    """
    meta: dict = {"title": "", "created": "", "updated": "", "tags": []}
    if not text.startswith("---"):
        return meta, text.strip()

    lines = text.splitlines()
    # find the closing fence (second `---`)
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return meta, text.strip()

    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if key == "tags":
            meta["tags"] = _parse_tags(val)
        elif key in meta:
            meta[key] = val

    body = "\n".join(lines[end + 1:]).strip()
    return meta, body


def _render_note(title: str, created: str, updated: str, tags: list[str], body: str) -> str:
    return (
        "---\n"
        f"title: {title}\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"tags: {_format_tags(tags)}\n"
        "---\n\n"
        f"{body.strip()}\n"
    )


def read_note(slug: str) -> dict | None:
    """Read one note by slug, returning its parsed fields + body, or None."""
    path = os.path.join(MEMORY_DIR, f"{slug}.md")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    meta, body = parse_note(text)
    return {
        "slug": slug,
        "title": meta["title"] or slug,
        "created": meta["created"],
        "updated": meta["updated"],
        "tags": meta["tags"],
        "body": body,
    }


def list_notes() -> list[dict]:
    """Return frontmatter-only metadata for every note in the vault,
    newest-updated first. The body is NOT loaded — this is the index."""
    if not os.path.isdir(MEMORY_DIR):
        return []
    notes = []
    for fn in os.listdir(MEMORY_DIR):
        if not fn.endswith(".md") or fn.startswith("_"):
            continue
        slug = fn[:-3]
        note = read_note(slug)
        if note is None:
            continue
        notes.append({
            "slug": note["slug"],
            "title": note["title"],
            "tags": note["tags"],
            "created": note["created"],
            "updated": note["updated"],
        })
    notes.sort(key=lambda n: n["updated"], reverse=True)
    return notes


def write_note(title: str, content: str, tags: list[str] | None = None) -> dict:
    """Create or UPDATE a note. If a note with the same slug exists, append
    the new content to its body and bump `updated` (and union the tags),
    rather than creating a duplicate file. Returns details of what happened.
    """
    os.makedirs(MEMORY_DIR, exist_ok=True)
    tags = tags or []
    slug = slugify(title)
    path = os.path.join(MEMORY_DIR, f"{slug}.md")
    today = date.today().isoformat()

    existing = read_note(slug)
    if existing is not None:
        action = "updated"
        created = existing["created"] or today
        # union tags, preserving existing order then new ones
        merged_tags = list(existing["tags"])
        for t in tags:
            if t not in merged_tags:
                merged_tags.append(t)
        # append the new content as a dated addition to the existing body
        body = existing["body"].rstrip()
        if body:
            body = f"{body}\n\n*(updated {today})*\n{content.strip()}"
        else:
            body = content.strip()
        title = existing["title"] or title
        text = _render_note(title, created, today, merged_tags, body)
        final_tags = merged_tags
    else:
        action = "created"
        text = _render_note(title, today, today, tags, content)
        final_tags = tags

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)  # atomic on same filesystem

    return {
        "action": action,
        "slug": slug,
        "title": title,
        "path": path,
        "tags": final_tags,
        "updated": today,
    }


# Common words that carry no retrieval signal — dropped so a query like
# "what am I prepping for" doesn't match every note on filler words.
_STOPWORDS = {
    "a", "an", "the", "is", "am", "are", "was", "were", "be", "been",
    "i", "you", "he", "she", "it", "we", "they", "my", "me", "your",
    "what", "which", "who", "whom", "when", "where", "why", "how",
    "for", "to", "of", "in", "on", "at", "by", "with", "about", "from",
    "do", "did", "does", "that", "this", "these", "those", "and", "or",
    "have", "has", "had", "can", "could", "should", "would", "will",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _query_terms(query: str) -> list[str]:
    return [t for t in _tokens(query) if len(t) >= 2 and t not in _STOPWORDS]


def _shares_stem(a: str, b: str, n: int = 4) -> bool:
    """True if a and b share a prefix of at least n chars — a cheap stand-in
    for stemming so 'applied' matches 'application' (shared 'appli')."""
    if len(a) < n or len(b) < n:
        return False
    common = os.path.commonprefix([a, b])
    return len(common) >= n


def _term_matches(term: str, words: list[str]) -> bool:
    """A term matches a note word on equality, a substring overlap (one
    contains the other, both >= 3 chars, e.g. 'prepping' vs 'prep'), or a
    shared word stem (e.g. 'applied' vs 'application')."""
    for w in words:
        if term == w:
            return True
        if len(term) >= 3 and len(w) >= 3 and (term in w or w in term):
            return True
        if _shares_stem(term, w):
            return True
    return False


def search_notes(query: str, limit: int = 3) -> list[dict]:
    """Simple no-embedding retrieval. Score each note on keyword overlap of
    the query against its title, tags, and slug (word-level, stopwords
    dropped); return the full content of the best matches, best first."""
    q = query.lower().strip()
    terms = _query_terms(query)
    scored = []
    for meta in list_notes():
        title_lower = meta["title"].lower()
        note_words = _tokens(title_lower) + _tokens(meta["slug"])
        for t in meta["tags"]:
            note_words += _tokens(t)
        tag_set = {t.lower() for t in meta["tags"]}

        score = 0
        # whole-query substring match in the title is the strongest signal
        if q and q in title_lower:
            score += 10
        for term in terms:
            if term in tag_set:           # exact tag hit is a strong signal
                score += 2
            elif _term_matches(term, note_words):
                score += 1
        if score > 0:
            scored.append((score, meta["slug"]))

    scored.sort(key=lambda s: s[0], reverse=True)
    results = []
    for _, slug in scored[:limit]:
        note = read_note(slug)
        if note is not None:
            results.append(note)
    return results
