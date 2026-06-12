import os
import uuid
from pathlib import Path

import httpx
import psycopg2
from fastapi import FastAPI, File, HTTPException, UploadFile
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
COLLECTION = "documents"
EMBED_DIM = 768

_PG_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'athena')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'athena')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres.athena.svc.cluster.local')}:5432"
    f"/{os.getenv('POSTGRES_DB', 'athena')}"
)


def pg_conn():
    return psycopg2.connect(_PG_DSN)


app = FastAPI(title="Athena Ingestion")
qdrant = QdrantClient(url=QDRANT_URL)


def _ensure_collection() -> None:
    existing = {c.name for c in qdrant.get_collections().collections}
    if COLLECTION not in existing:
        qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )


@app.on_event("startup")
async def startup() -> None:
    _ensure_collection()
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


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

    return {
        "document_id": document_id,
        "filename": original_filename,
        "chunks": chunk_count,
        "summary": summary,
    }


@app.post("/ingest", response_model=IngestResponse)
async def ingest(file: UploadFile = File(...)) -> IngestResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file is missing a filename.")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    dest = DOCS_DIR / file.filename
    dest.write_bytes(await file.read())
    result = _ingest_path(dest, file.filename)
    return IngestResponse(**result)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
