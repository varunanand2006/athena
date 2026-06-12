import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg2
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from llama_index.core import SimpleDirectoryReader
from llama_index.core.node_parser import SentenceSplitter
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama.athena.svc.cluster.local:11434")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant.athena.svc.cluster.local:6333")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")
DOCS_DIR = Path(os.getenv("INGESTION_DOCS_DIR", "/data/documents"))
TOC_FILENAME = "_TABLE_OF_CONTENTS.md"
WATCH_INTERVAL_MINUTES = int(os.getenv("INGESTION_WATCH_INTERVAL_MINUTES", "5"))
COLLECTION = "documents"
EMBED_DIM = 768

log = logging.getLogger("ingestion")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_PG_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'athena')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'athena')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres.athena.svc.cluster.local')}:5432"
    f"/{os.getenv('POSTGRES_DB', 'athena')}"
)


def pg_conn():
    return psycopg2.connect(_PG_DSN)


qdrant = QdrantClient(url=QDRANT_URL)


def _ensure_collection() -> None:
    existing = {c.name for c in qdrant.get_collections().collections}
    if COLLECTION not in existing:
        qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_collection()
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _scan_documents_folder,
        "interval",
        minutes=WATCH_INTERVAL_MINUTES,
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("folder watcher started — scanning %s every %s min", DOCS_DIR, WATCH_INTERVAL_MINUTES)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Athena Ingestion", lifespan=lifespan)


def _embed(text: str) -> list[float]:
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]


SUMMARY_PROMPT = """Summarize what this document is in 2-3 sentences. Be specific about its contents and purpose. Do not say "I" or explain yourself.

Document:
{text}"""


def _generate_summary(text: str) -> str:
    snippet = text[:2000]
    with httpx.Client(timeout=90) as client:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "user", "content": SUMMARY_PROMPT.format(text=snippet)}
                ],
                "think": False,
                "stream": False,
                "options": {"num_ctx": 2048, "num_predict": 150},
            },
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "").strip()


class IngestResponse(BaseModel):
    filename: str
    chunks: int
    document_id: str
    summary: str


def _ingest_path(file_path: Path, original_filename: str) -> dict:
    """Catalog and embed a file already present on disk at file_path.

    Shared by POST /ingest (after writing the upload to the PVC) and the
    folder watcher. On re-ingest of an existing filename, the old catalog
    row and its Qdrant points are dropped first.
    """
    doc_type = file_path.suffix.lstrip(".").lower() or "bin"
    title = file_path.stem
    size_bytes = file_path.stat().st_size

    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM documents WHERE filename = %s", (original_filename,))
            row = cur.fetchone()
            if row:
                old_id = str(row[0])
                qdrant.delete(
                    collection_name=COLLECTION,
                    points_selector=FilterSelector(
                        filter=Filter(
                            must=[FieldCondition(
                                key="document_id",
                                match=MatchValue(value=old_id),
                            )]
                        )
                    ),
                )
                cur.execute("DELETE FROM documents WHERE id = %s", (old_id,))
                conn.commit()

            cur.execute(
                """
                INSERT INTO documents
                    (filename, title, doc_type, file_path, size_bytes)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (original_filename, title, doc_type, str(file_path), size_bytes),
            )
            document_id = str(cur.fetchone()[0])
            conn.commit()
    finally:
        conn.close()

    docs = SimpleDirectoryReader(input_files=[str(file_path)]).load_data()
    if not docs:
        conn = pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM documents WHERE id = %s", (document_id,))
                conn.commit()
        finally:
            conn.close()
        raise HTTPException(status_code=422, detail="Could not extract text from file.")

    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)
    nodes = splitter.get_nodes_from_documents(docs)

    points = []
    full_text_parts = []
    for node in nodes:
        text = node.get_content().strip()
        if not text:
            continue
        full_text_parts.append(text)
        vector = _embed(text)
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "text": text,
                "filename": original_filename,
                "document_id": document_id,
            },
        ))

    if points:
        qdrant.upsert(collection_name=COLLECTION, points=points)

    chunk_count = len(points)
    full_text = "\n\n".join(full_text_parts)
    summary = ""
    if full_text:
        try:
            summary = _generate_summary(full_text)
        except Exception:
            summary = ""

    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET summary = %s, chunk_count = %s WHERE id = %s",
                (summary, chunk_count, document_id),
            )
            conn.commit()
    finally:
        conn.close()

    try:
        _regenerate_toc()
    except Exception:
        pass

    return {
        "document_id": document_id,
        "filename": original_filename,
        "chunks": chunk_count,
        "summary": summary,
    }


def _regenerate_toc() -> None:
    """Rebuild the human-readable markdown table of contents on the PVC.

    Writes atomically via a .tmp + os.replace so the folder watcher never
    sees a half-written file.
    """
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title, doc_type, added_at, summary FROM documents ORDER BY added_at DESC"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Athena Document Library",
        f"_Last updated: {timestamp}_",
        "",
        f"{len(rows)} documents stored.",
        "",
        "| Title | Type | Added | Summary |",
        "|-------|------|-------|---------|",
    ]
    for title, doc_type, added_at, summary in rows:
        added = added_at.strftime("%Y-%m-%d") if added_at else ""
        clean_summary = (summary or "").replace("|", "\\|").replace("\n", " ").strip()
        clean_title = (title or "").replace("|", "\\|")
        lines.append(f"| {clean_title} | {doc_type} | {added} | {clean_summary} |")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    final = DOCS_DIR / TOC_FILENAME
    tmp = DOCS_DIR / f"{TOC_FILENAME}.tmp"
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, final)


@app.post("/ingest", response_model=IngestResponse)
async def ingest(file: UploadFile = File(...)) -> IngestResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file is missing a filename.")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOCS_DIR / file.filename
    dest.write_bytes(await file.read())
    result = _ingest_path(dest, file.filename)
    return IngestResponse(**result)


def _scan_documents_folder() -> None:
    """Find files on the PVC that aren't in the catalog and ingest them.

    Runs every WATCH_INTERVAL_MINUTES minutes via the lifespan scheduler.
    Skips the TOC file, anything starting with `_`, and `.tmp` partials.
    Logs and continues on per-file failures so one bad file can't stall
    the whole scan.
    """
    if not DOCS_DIR.exists():
        return

    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT filename FROM documents")
            known = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    for entry in DOCS_DIR.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if name.startswith("_") or name.endswith(".tmp") or name == TOC_FILENAME:
            continue
        if name in known:
            continue
        try:
            log.info("auto-ingesting %s", name)
            _ingest_path(entry, name)
        except Exception:
            log.exception("auto-ingest failed for %s", name)


@app.get("/toc", response_class=PlainTextResponse)
def toc() -> str:
    path = DOCS_DIR / TOC_FILENAME
    if not path.exists():
        _regenerate_toc()
    return path.read_text(encoding="utf-8")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
