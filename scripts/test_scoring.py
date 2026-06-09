import httpx

OLLAMA_URL = "http://ollama.athena.svc.cluster.local:11434"
MODEL = "gemma4:e2b"

prompt = """You are an internship advisor for a junior CS student (graduating Dec 2027) with experience in:
- AI & Agents: Local LLMs, Python, RAG pipelines, LangGraph, Qdrant
- Cloud & Infra: Kubernetes, Docker, Linux, self-hosted systems
- Systems & Low-Level: Rust, C
- General SWE: Full-stack, algorithms, data structures

Rate this internship posting from 1-10 based on fit. Then pick the best resume.

Company: Etched
Role: Inference Intern, Architecture
Location: San Jose, CA
Company summary: Etched builds custom AI inference chips focused on transformer model acceleration.

Respond in exactly this format, nothing else:
SCORE: <number>
RESUME: <one of: AI & Agents | Cloud & Infra | Systems & Low-Level | General SWE>"""

r = httpx.post(
    f"{OLLAMA_URL}/api/generate",
    json={"model": MODEL, "prompt": prompt, "stream": False,
          "options": {"num_ctx": 2048, "num_predict": 150}},
    timeout=120,
)
text = r.json()["response"]
print("=== RAW RESPONSE ===")
print(repr(text))
print("=== LINES ===")
for line in text.splitlines():
    print(repr(line))
