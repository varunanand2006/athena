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
    source: explicit
    origin: conversation
    tags: [interview, meta]
    events: [{date: 2026-06-19, kind: interview}]
    ---

    Varun is preparing for a Meta software engineering interview...

Rules:
  * `title`   — the human-readable note title (required).
  * `created` — ISO date (YYYY-MM-DD) the note was first written.
  * `updated` — ISO date of the most recent write; bumped on every update.
  * `source`  — "explicit" (user said "remember…") or "auto" (Phase 15
                autonomous reflection). Records how the note ORIGINATED;
                preserved across updates. Missing source -> "explicit".
  * `origin`  — (Phase 21) WHERE the note came from: "conversation" (chat
                reflection or an explicit chat write), "calendar" (the
                background calendar sweep), or "email" (the background labeled-
                email sweep). Makes the vault auditable — the /memory view shows
                a "from calendar / from email / from conversation" chip so the
                user can spot and delete a feed-captured note. Like `source` it
                is a property of the FIRST write and preserved across updates.
                Missing origin -> "conversation" (backward compatible).
  * `tags`    — a YAML list; may be empty (`tags: []`).
  * `events`  — (Phase 17) an OPTIONAL YAML list of {date, kind} maps, the
                only structured/queryable field we extract from a note's prose
                when it concerns something time-bound (an interview, a
                deadline, an application). `date` is ISO (YYYY-MM-DD); `kind`
                is a short label. Missing/empty -> `[]` (every pre-Phase-17
                note is dateless by definition). Merged across same-slug
                updates like `tags`. The note stays the single source of
                truth — there is deliberately no separate events table (see
                ADR 009).
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
from datetime import date, datetime, timezone

MEMORY_DIR = os.getenv("MEMORY_DIR", "/data/memory")

# Phase 16 — the whole vault is loaded into the chat agent's system prompt each
# turn (ambient recall). This cap is the honest, named tripwire for the FUTURE
# embeddings phase: when the assembled block exceeds it we still load up to the
# cap, but flag over_cap so /system and the logs surface "vault too big for
# full-context load". Generous default; env-overridable so it can be tuned
# without a rebuild.
MEMORY_CONTEXT_MAX_TOKENS = int(os.getenv("MEMORY_CONTEXT_MAX_TOKENS", "8000"))

# Phase 17 — upcoming() does a full-vault frontmatter scan (same pattern as the
# Phase 16 full-vault load). This is the parallel tripwire: past it, log "vault
# too big for frontmatter scan — time for a derived index".
MEMORY_EVENTS_MAX_NOTES = int(os.getenv("MEMORY_EVENTS_MAX_NOTES", "500"))


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


# --- events (Phase 17) -----------------------------------------------------
# Same hand-rolled spirit as tags: the format is fixed and tiny, so we parse it
# without PyYAML. An event is a `{date: YYYY-MM-DD, kind: <short>}` flow map and
# the field is a flow list of them: `events: [{date: ..., kind: ...}, {...}]`.
# This is valid YAML flow style, so the vault stays Obsidian-compatible.

_EVENT_RE = re.compile(r"\{([^}]*)\}")


def _parse_events(raw: str) -> list[dict]:
    """Parse an `events:` value into a list of {date, kind} dicts.

    Tolerant: only well-formed maps with a non-empty `date` are kept; anything
    malformed is dropped (a missed date just means the note isn't
    time-queryable — still exists as prose). `events` is a DERIVED field,
    rebuildable by re-scanning the vault, so dropping garbage is safe."""
    events = []
    for inner in _EVENT_RE.findall(raw):
        fields = {}
        for part in inner.split(","):
            if ":" not in part:
                continue
            k, _, v = part.partition(":")
            fields[k.strip()] = v.strip()
        date = fields.get("date", "")
        if not date:
            continue
        events.append({"date": date, "kind": fields.get("kind", "")})
    return events


def _format_events(events: list[dict]) -> str:
    parts = [
        f"{{date: {e.get('date', '')}, kind: {e.get('kind', '')}}}"
        for e in events
        if e.get("date")
    ]
    return "[" + ", ".join(parts) + "]"


def _merge_events(existing: list[dict], new: list[dict]) -> list[dict]:
    """Union events across a same-slug update, deduping on (date, kind) —
    mirrors how tags are merged so re-reflection doesn't duplicate a deadline."""
    merged = list(existing)
    seen = {(e.get("date"), e.get("kind")) for e in merged}
    for e in new:
        key = (e.get("date"), e.get("kind"))
        if e.get("date") and key not in seen:
            merged.append({"date": e["date"], "kind": e.get("kind", "")})
            seen.add(key)
    return merged


def sanitize_events(raw) -> list[dict]:
    """Validate a list of {date, kind} event maps: keep only items with a real
    ISO (YYYY-MM-DD) date, coercing `kind` to a short string. Malformed or
    unresolvable dates are dropped (the note still exists as prose) rather than
    written as a broken event — getting a date wrong must stay cheap.

    Single source of truth for event hygiene, shared by the foreground
    correction tool (update_memory, Phase 21) and the background sweeps so
    validation is identical no matter who emits the events."""
    if not isinstance(raw, list):
        return []
    clean = []
    for ev in raw:
        if not isinstance(ev, dict):
            continue
        d = str(ev.get("date", "")).strip()
        try:
            date.fromisoformat(d)
        except ValueError:
            continue
        kind = str(ev.get("kind", "")).strip()[:40]
        clean.append({"date": d, "kind": kind})
    return clean


def parse_note(text: str) -> tuple[dict, str]:
    """Split a note's raw text into (frontmatter dict, body).

    Tolerant of missing frontmatter: if the file doesn't start with a
    `---` fence we treat the whole thing as the body with empty metadata.
    """
    # `source` defaults to "explicit": any note written before Phase 15 (or
    # with no source line) was, by definition, an explicit user-driven write.
    # `origin` defaults to "conversation": every note written before Phase 21
    # (no origin line) came from conversation reflection or an explicit chat
    # write, so that's the backward-compatible default.
    meta: dict = {
        "title": "", "created": "", "updated": "", "source": "explicit",
        "origin": "conversation", "tags": [], "events": [],
    }
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
        elif key == "events":
            meta["events"] = _parse_events(val)
        elif key in meta:
            meta[key] = val

    body = "\n".join(lines[end + 1:]).strip()
    return meta, body


def _render_note(
    title: str, created: str, updated: str, tags: list[str], body: str,
    source: str = "explicit", events: list[dict] | None = None,
    origin: str = "conversation",
) -> str:
    return (
        "---\n"
        f"title: {title}\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"source: {source}\n"
        f"origin: {origin}\n"
        f"tags: {_format_tags(tags)}\n"
        f"events: {_format_events(events or [])}\n"
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
        "source": meta["source"] or "explicit",
        "origin": meta["origin"] or "conversation",
        "tags": meta["tags"],
        "events": meta["events"],
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
            "events": note["events"],
            "created": note["created"],
            "updated": note["updated"],
            "source": note["source"],
            "origin": note["origin"],
        })
    notes.sort(key=lambda n: n["updated"], reverse=True)
    return notes


def write_note(
    title: str, content: str, tags: list[str] | None = None,
    source: str = "explicit", events: list[dict] | None = None,
    replace: bool = False, replace_events: bool = False,
    origin: str = "conversation",
) -> dict:
    """Create or UPDATE a note. If a note with the same slug exists, append
    the new content to its body and bump `updated` (and union the tags and
    events), rather than creating a duplicate file. Returns details of what
    happened.

    `source` ("explicit" | "auto") records how the note ORIGINATED. On update
    the existing note's source is PRESERVED — origin is a property of the first
    write, so an auto-reflection touching a user-written note keeps it
    "explicit" (and vice versa). This keeps the frontend's source badge honest
    about whether the agent created a note on its own initiative.

    `events` (Phase 17) is an optional list of {date, kind} maps extracted from
    the note's prose. Merged across same-slug updates the same way tags are, so
    a re-reflection adds new deadlines without dropping or duplicating old ones.

    `replace` (Phase 18) controls update semantics on an existing note:
      * False (default) — APPEND `content` as a dated addition (Phase 15
        behavior; preserves the audit trail for user-fact notes).
      * True — RECONCILE: replace the body entirely with `content`. Used for
        wiki concept/entity pages, where synthesis hands back a clean rewritten
        page rather than an ever-growing append log. `created`/`source` are
        still preserved; tags/events still merge. The `_log.md` op log
        (append_log) is the audit trail that makes destructive rewrites safe.

    `replace_events` (Phase 21) controls event-list semantics on an existing
    note:
      * False (default) — UNION the supplied events with the note's existing
        ones (so re-reflection adds deadlines without dropping old ones).
      * True — REPLACE the note's events with exactly the supplied list. This is
        the foreground CORRECTION path (update_memory): when the user says an
        interview "moved to Thursday", the note must show ONLY Thursday, not
        Monday+Thursday. ONLY the foreground explicit-correction path passes
        this — background reflection NEVER does (it would risk gemma silently
        dropping good dates; the destructive-event-rewrite capability is
        deliberately foreground-only, mirroring the Part 1 safety boundary).

    `origin` (Phase 21) records WHERE a note came from — "conversation"
    (default), "calendar", or "email". Set on CREATE by the background external-
    source sweeps; on UPDATE the existing note's origin is PRESERVED (a property
    of the first write, exactly like `source`), so the /memory provenance chip
    stays honest even when a feed later touches a conversation note.
    """
    os.makedirs(MEMORY_DIR, exist_ok=True)
    tags = tags or []
    events = events or []
    slug = slugify(title)
    path = os.path.join(MEMORY_DIR, f"{slug}.md")
    today = date.today().isoformat()

    existing = read_note(slug)
    if existing is not None:
        action = "reconciled" if replace else "updated"
        created = existing["created"] or today
        # union tags, preserving existing order then new ones
        merged_tags = list(existing["tags"])
        for t in tags:
            if t not in merged_tags:
                merged_tags.append(t)
        if replace_events:
            # Foreground correction: the supplied events stand alone (dedup +
            # drop dateless), overwriting the note's old dates instead of
            # accumulating both the stale and corrected ones.
            merged_events = _merge_events([], events)
        else:
            merged_events = _merge_events(existing["events"], events)
        if replace:
            # Reconcile: clean rewrite of the page body.
            body = content.strip()
        else:
            # append the new content as a dated addition to the existing body
            body = existing["body"].rstrip()
            if body:
                body = f"{body}\n\n*(updated {today})*\n{content.strip()}"
            else:
                body = content.strip()
        title = existing["title"] or title
        note_source = existing["source"] or "explicit"  # preserve how it originated
        note_origin = existing.get("origin") or "conversation"  # preserve provenance
        text = _render_note(
            title, created, today, merged_tags, body, note_source,
            merged_events, note_origin,
        )
        final_tags = merged_tags
        final_events = merged_events
    else:
        action = "created"
        note_source = source
        note_origin = origin
        final_events = _merge_events([], events)  # dedup + drop dateless
        text = _render_note(title, today, today, tags, content, source, final_events, origin)
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
        "events": final_events,
        "source": note_source,
        "origin": note_origin,
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


# --- Ambient recall: full-vault context block (Phase 16) -------------------


def _approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token). Deliberately NOT tiktoken: this
    feeds a tripwire and a /system gauge that are mostly future-proofing today,
    so the heuristic avoids a dependency to sync across pyproject + Dockerfile."""
    return max(1, len(text) // 4) if text else 0


def _render_note_for_context(note: dict) -> str:
    """One note rendered for the system-prompt memory block: title + tags +
    any events + body. Compact but complete — recall is the model reasoning
    over this text, so it gets the whole note, not a summary."""
    tag_str = f" [{', '.join(note['tags'])}]" if note["tags"] else ""
    lines = [f"## {note['title']}{tag_str}", f"(updated {note['updated']})"]
    if note["events"]:
        lines.append(f"events: {_format_events(note['events'])}")
    lines.append("")
    lines.append(note["body"])
    return "\n".join(lines).strip()


def assemble_memory_context() -> dict:
    """Read the ENTIRE vault and format it into one block for the chat agent's
    system prompt (Phase 16 ambient recall). Returns the block plus measurements
    so the caller can enforce the cap, report cost, and watch the tripwire.

    This is the single home for the cap guardrail. `tokens` is always the FULL
    vault size (the number to watch as it approaches the cap); when it exceeds
    MEMORY_CONTEXT_MAX_TOKENS we set `over_cap` and trim whole notes (oldest
    `updated` first — list_notes is newest-first) so we load UP TO the cap
    rather than overflowing the context. The honest, named trigger for the
    future embeddings phase — we do NOT build embeddings here.
    """
    notes = list_notes()  # newest-updated first, frontmatter only
    rendered = []
    for meta in notes:
        note = read_note(meta["slug"])
        if note is not None:
            rendered.append(_render_note_for_context(note))

    full_block = "\n\n".join(rendered)
    tokens = _approx_tokens(full_block)
    over_cap = tokens > MEMORY_CONTEXT_MAX_TOKENS

    if over_cap:
        kept, running = [], 0
        for block in rendered:
            t = _approx_tokens(block)
            if running + t > MEMORY_CONTEXT_MAX_TOKENS:
                break
            kept.append(block)
            running += t
        block_text = "\n\n".join(kept)
    else:
        block_text = full_block

    return {
        "block": block_text,
        "tokens": tokens,            # full-vault size, even when trimmed
        "note_count": len(notes),
        "max_tokens": MEMORY_CONTEXT_MAX_TOKENS,
        "over_cap": over_cap,
    }


# --- Temporal recall: full-vault events scan (Phase 17) --------------------


def collect_events() -> tuple[list[dict], int, bool]:
    """Scan every note's `events` frontmatter and return a flat list of events
    (each annotated with its note's title + slug), the number of notes scanned,
    and whether the scan tripped MEMORY_EVENTS_MAX_NOTES.

    Deliberately the same full-vault-scan pattern as assemble_memory_context;
    the tripwire is the honest signal that the vault has outgrown a linear scan
    and wants a derived index — we do NOT build that index here.
    """
    notes = list_notes()
    over_cap = len(notes) > MEMORY_EVENTS_MAX_NOTES
    out = []
    for meta in notes:
        for ev in meta["events"]:
            if not ev.get("date"):
                continue
            out.append({
                "date": ev["date"],
                "kind": ev.get("kind", ""),
                "title": meta["title"],
                "slug": meta["slug"],
            })
    return out, len(notes), over_cap


# --- Interlinked wiki: the graph (Phase 18) --------------------------------
# The graph is DERIVED from prose: `[[wikilinks]]` live in note bodies (where
# synthesis authors them and Obsidian renders them), and the edges are computed
# by scanning, never stored in a second place that could drift. Same philosophy
# as `events` — one source of truth, rebuildable by re-scanning. Link identity
# is the slug (slugify of the link target), matching the note-identity rule, so
# `[[Meta interview prep]]` resolves to `meta-interview-prep.md`.

# `[[Target]]` or `[[Target|Display alias]]` (Obsidian syntax). We capture the
# target (before any `|`); display text is the frontend's concern.
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def extract_links(body: str) -> list[dict]:
    """Outgoing links from a note body. Returns deduped [{slug, target}] where
    `target` is the raw link text and `slug` is its resolved note identity."""
    seen = {}
    for raw in _WIKILINK_RE.findall(body or ""):
        target = raw.split("|", 1)[0].strip()
        if not target:
            continue
        slug = slugify(target)
        if slug not in seen:
            seen[slug] = {"slug": slug, "target": target}
    return list(seen.values())


def backlinks(slug: str) -> list[dict]:
    """Incoming links: notes whose body links to `slug`. Full-vault scan
    (deliberately the same pattern as the Phase 16 load / Phase 17 events scan);
    the graph is small enough that scanning is fine, and there's no index to
    keep in sync."""
    out = []
    for meta in list_notes():
        if meta["slug"] == slug:
            continue
        note = read_note(meta["slug"])
        if note is None:
            continue
        if any(l["slug"] == slug for l in extract_links(note["body"])):
            out.append({"slug": note["slug"], "title": note["title"]})
    return out


# --- Wiki artifacts: index + op log (Phase 18) -----------------------------
# `_`-prefixed so list_notes() skips them (same convention as the documents'
# `_TABLE_OF_CONTENTS.md`). Generated/derived — never hand-authored memory.

_INDEX_FILE = "_index.md"
_LOG_FILE = "_log.md"


def write_index() -> None:
    """Regenerate `_index.md` — the wiki catalog (Karpathy's index.md). Lists
    every note newest-first with tags and event/link counts. Derived, so it's
    safe to overwrite wholesale (atomic write, like the documents TOC)."""
    notes = list_notes()
    lines = ["# Memory Wiki Index", "", f"_{len(notes)} notes._", ""]
    for n in notes:
        tag_str = f" [{', '.join(n['tags'])}]" if n["tags"] else ""
        ev = f" · {len(n['events'])} event(s)" if n["events"] else ""
        lines.append(f"- [[{n['title']}]]{tag_str} (updated {n['updated']}){ev}")
    text = "\n".join(lines) + "\n"

    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = os.path.join(MEMORY_DIR, _INDEX_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def append_log(entry: str) -> None:
    """Append one timestamped line to `_log.md` — the operation log (Karpathy's
    log.md). This is the audit trail that makes reconciling (destructive
    body-rewrite) safe: every synthesis op is recorded even though the page
    itself was overwritten."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = os.path.join(MEMORY_DIR, _LOG_FILE)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"- {stamp}  {entry}\n"
    # Create with a header the first time so the file reads cleanly in Obsidian.
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Memory Wiki Log\n\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
