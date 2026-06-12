import logging
import os
import threading
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


def _insert_catalog_row(file_path: Path, original_filename: str) -> str:
    """Synchronous setup: handle re-ingest cleanup and insert the catalog row.

    Returns the new document_id. Fast — only DB + Qdrant filter-delete, no
    embedding or summarization. Safe to call from request handlers without
    blocking the client.
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
    return document_id


def _embed_and_summarize(document_id: str, file_path: Path, original_filename: str) -> None:
    """Heavy work: chunk, embed, summarize, update catalog, regenerate TOC.

    Runs in a background thread for POST /ingest so big files don't block
    the client past the proxy timeout. The watcher calls it inline since
    it already runs in the APScheduler thread.

    Logs and recovers on failure rather than raising. If the catalog row
    is left with chunk_count=0 and no summary, the row stays as evidence
    of the failure and the user can delete + re-upload.
    """
    try:
        docs = SimpleDirectoryReader(input_files=[str(file_path)]).load_data()
    except Exception:
        log.exception("text extraction failed for %s", original_filename)
        return
    if not docs:
        log.warning("text extraction produced no content for %s", original_filename)
        return

    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)
    nodes = splitter.get_nodes_from_documents(docs)

    points = []
    full_text_parts = []
    for node in nodes:
        text = node.get_content().strip()
        if not text:
            continue
        full_text_parts.append(text)
        try:
            vector = _embed(text)
        except Exception:
            log.exception("embedding call failed for %s chunk", original_filename)
            return
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
        try:
            qdrant.upsert(collection_name=COLLECTION, points=points)
        except Exception:
            log.exception("qdrant upsert failed for %s", original_filename)
            return

    chunk_count = len(points)
    full_text = "\n\n".join(full_text_parts)
    summary = ""
    if full_text:
        try:
            summary = _generate_summary(full_text)
        except Exception:
            log.exception("summary generation failed for %s", original_filename)
            summary = ""

    try:
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
    except Exception:
        log.exception("catalog update failed for %s", original_filename)
        return

    try:
        _regenerate_toc()
    except Exception:
        log.exception("toc regeneration failed after ingest of %s", original_filename)

    log.info("ingest complete for %s (%s chunks)", original_filename, chunk_count)


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
    """Save the upload and kick off background processing.

    Returns immediately after the file is on the PVC and a catalog row is
    inserted with chunk_count=0 and no summary. Embedding + summary run in
    a daemon thread so large files don't blow past the proxy timeout.
    The frontend polls /documents and the row fills in when processing
    completes.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file is missing a filename.")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOCS_DIR / file.filename
    dest.write_bytes(await file.read())
    document_id = _insert_catalog_row(dest, file.filename)
    threading.Thread(
        target=_embed_and_summarize,
        args=(document_id, dest, file.filename),
        daemon=True,
        name=f"ingest-{file.filename}",
    ).start()
    return IngestResponse(
        filename=file.filename,
        chunks=0,
        document_id=document_id,
        summary="",
    )


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
            document_id = _insert_catalog_row(entry, name)
            _embed_and_summarize(document_id, entry, name)
        except Exception:
            log.exception("auto-ingest failed for %s", name)


@app.get("/toc", response_class=PlainTextResponse)
def toc() -> str:
    path = DOCS_DIR / TOC_FILENAME
    if not path.exists():
        _regenerate_toc()
    return path.read_text(encoding="utf-8")


@app.delete("/ingest/documents/{document_id}")
def delete_document(document_id: str) -> dict:
    """Remove a document from the PVC, the Qdrant collection, and the catalog.

    The file must be removed from the PVC too — leaving it in place would
    cause the folder watcher to re-ingest it on the next scan.
    """
    conn = pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT file_path FROM documents WHERE id = %s", (document_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="document not found")
            file_path = row[0]

            qdrant.delete(
                collection_name=COLLECTION,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[FieldCondition(
                            key="document_id",
                            match=MatchValue(value=document_id),
                        )]
                    )
                ),
            )
            cur.execute("DELETE FROM documents WHERE id = %s", (document_id,))
            conn.commit()
    finally:
        conn.close()

    try:
        Path(file_path).unlink(missing_ok=True)
    except Exception:
        log.exception("failed to delete file %s", file_path)

    try:
        _regenerate_toc()
    except Exception:
        pass

    return {"deleted": document_id}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
