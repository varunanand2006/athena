import httpx, json

OLLAMA_URL = "http://ollama.athena.svc.cluster.local:11434"
MODEL = "gemma4:e2b"

# Test 1: short prompt via chat
r = httpx.post(f"{OLLAMA_URL}/api/chat",
    json={"model": MODEL, "messages": [{"role": "user", "content": "Say hello."}],
          "stream": False, "options": {"num_predict": 50}},
    timeout=120)
print("=== SHORT PROMPT (chat) ===")
print(json.dumps(r.json(), indent=2)[:500])

# Test 2: medium prompt via chat, no num_ctx
r2 = httpx.post(f"{OLLAMA_URL}/api/chat",
    json={"model": MODEL,
          "messages": [{"role": "user", "content": "Rate this job from 1-10 and say why. Job: Software Engineer at Google, skills needed: Python.\nRespond with just: SCORE: <number>"}],
          "stream": False, "options": {"num_predict": 50}},
    timeout=120)
print("\n=== MEDIUM PROMPT, no num_ctx (chat) ===")
print(json.dumps(r2.json(), indent=2)[:500])

# Test 3: same as test 2 but with num_ctx: 2048
r3 = httpx.post(f"{OLLAMA_URL}/api/chat",
    json={"model": MODEL,
          "messages": [{"role": "user", "content": "Rate this job from 1-10 and say why. Job: Software Engineer at Google, skills needed: Python.\nRespond with just: SCORE: <number>"}],
          "stream": False, "options": {"num_ctx": 2048, "num_predict": 50}},
    timeout=120)
print("\n=== MEDIUM PROMPT, with num_ctx: 2048 (chat) ===")
print(json.dumps(r3.json(), indent=2)[:500])
