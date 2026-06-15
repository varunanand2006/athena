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

MEMORY_DIR = os.getenv("MEMORY_DIR", "/data/memory")


def slugify(title: str) -> str:
    """Convert a note title into its canonical filename slug.

    Lowercase, non-alphanumeric runs collapse to single hyphens, leading
    and trailing hyphens stripped. The slug is the note's stable identity
    for update-vs-create: same slug == same note.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"
