import os
import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from llama_index.core import SimpleDirectoryReader, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama.athena.svc.cluster.local:11434")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant.athena.svc.cluster.local:6333")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
COLLECTION = "documents"
EMBED_DIM = 768

app = FastAPI(title="Athena Ingestion")

qdrant = QdrantClient(url=QDRANT_URL)

embed_model = OllamaEmbedding(
    model_name=EMBED_MODEL,
    base_url=OLLAMA_BASE_URL,
)


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


class IngestResponse(BaseModel):
    filename: str
    chunks: int


@app.post("/ingest", response_model=IngestResponse)
async def ingest(file: UploadFile = File(...)) -> IngestResponse:
    suffix = Path(file.filename or "upload").suffix or ".bin"
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / f"upload{suffix}"
        dest.write_bytes(await file.read())

        docs = SimpleDirectoryReader(tmpdir).load_data()
        if not docs:
            raise HTTPException(status_code=422, detail="Could not extract text from file.")

        splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)
        nodes = splitter.get_nodes_from_documents(docs)

        vector_store = QdrantVectorStore(
            client=qdrant,
            collection_name=COLLECTION,
        )
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex(
            nodes,
            storage_context=storage_context,
            embed_model=embed_model,
            show_progress=False,
        )

    return IngestResponse(filename=file.filename or "unknown", chunks=len(nodes))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
