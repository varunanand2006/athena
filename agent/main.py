import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

_executor = ThreadPoolExecutor(max_workers=2)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama.athena.svc.cluster.local:11434")
SEARXNG_BASE_URL = os.getenv("SEARXNG_BASE_URL", "http://searxng.athena.svc.cluster.local:80")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant.athena.svc.cluster.local:6333")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e2b")

app = FastAPI(title="Athena Agent")

llm = ChatOllama(base_url=OLLAMA_BASE_URL, model=MODEL, temperature=0)


@tool
def web_search(query: str) -> str:
    """Search the web for current information using SearXNG."""
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{SEARXNG_BASE_URL}/search",
            params={"q": query, "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])[:5]
    if not results:
        return "No results found."

    lines = []
    for r in results:
        lines.append(f"- {r.get('title', 'No title')}: {r.get('url', '')}")
        if r.get("content"):
            lines.append(f"  {r['content'][:200]}")
    return "\n".join(lines)


@tool
def search_documents(query: str) -> str:
    """Search personal documents (resume, notes, project writeups) stored in Qdrant.
    Use this tool when the question is about the user's own background, skills, experience,
    projects, or anything that would be found in personal documents."""
    with httpx.Client(timeout=30) as client:
        embed_resp = client.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": query},
        )
        embed_resp.raise_for_status()
        vector = embed_resp.json()["embedding"]

        search_resp = client.post(
            f"{QDRANT_URL}/collections/documents/points/search",
            json={"vector": vector, "limit": 5, "with_payload": True},
        )
        search_resp.raise_for_status()
        hits = search_resp.json().get("result", [])

    if not hits:
        return "No relevant documents found."

    lines = []
    for hit in hits:
        payload = hit.get("payload", {})
        score = hit.get("score", 0)
        # LlamaIndex stores node text as a JSON string under _node_content
        node_content_raw = payload.get("_node_content", "")
        if node_content_raw:
            try:
                import json as _json
                text = _json.loads(node_content_raw).get("text", "").strip()
            except Exception:
                text = ""
        else:
            text = payload.get("text", "").strip()
        if text:
            lines.append(f"[score={score:.2f}] {text[:400]}")
    return "\n\n".join(lines) if lines else "No relevant documents found."


SYSTEM_PROMPT = (
    "You are Athena, a personal AI assistant. "
    "You have access to two tools: web_search and search_documents. "
    "For questions about the user's own background, resume, skills, projects, experience, "
    "or anything personal, you MUST call search_documents before answering. "
    "For questions about current events, job listings, prices, news, or anything "
    "that may have changed recently, you MUST call web_search before answering. "
    "Never say you cannot access information — use the appropriate tool instead."
)

agent = create_react_agent(llm, tools=[web_search, search_documents], prompt=SYSTEM_PROMPT)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor,
            lambda: agent.invoke({"messages": [{"role": "user", "content": req.message}]}),
        )
        last = result["messages"][-1]
        return ChatResponse(response=last.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
