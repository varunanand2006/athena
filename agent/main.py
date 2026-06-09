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


SYSTEM_PROMPT = (
    "You are Athena, a personal AI assistant. "
    "You have access to a web_search tool. "
    "For any question about current events, job listings, prices, news, or anything "
    "that may have changed recently, you MUST call web_search before answering. "
    "Never say you cannot access current information — use the tool instead."
)

agent = create_react_agent(llm, tools=[web_search], prompt=SYSTEM_PROMPT)


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
