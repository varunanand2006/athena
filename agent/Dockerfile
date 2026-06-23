FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "langgraph>=0.2" \
    "langchain-ollama>=0.2" \
    "langchain-openai>=0.2" \
    "langchain-core>=0.3" \
    "httpx>=0.27" \
    "psycopg2-binary>=2.9" \
    "apscheduler>=3.10" \
    "google-api-python-client>=2.0" \
    "google-auth>=2.0" \
    "prometheus-client>=0.20"

COPY main.py .
COPY metrics.py .
COPY memory.py .
COPY reflection.py .
COPY gmail_client.py .
COPY calendar_client.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
