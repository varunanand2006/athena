import httpx

r = httpx.post(
    "http://ollama.athena.svc.cluster.local:11434/api/generate",
    json={
        "model": "gemma4:e2b",
        "prompt": "Say hello.",
        "stream": False,
        "options": {"num_ctx": 2048, "num_predict": 50},
    },
    timeout=120,
)
data = r.json()
print("response:", repr(data.get("response")))
print("done:", data.get("done"))
