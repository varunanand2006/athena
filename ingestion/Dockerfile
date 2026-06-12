FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "llama-index-core>=0.11" \
    "llama-index-embeddings-ollama>=0.3" \
    "llama-index-vector-stores-qdrant>=0.3" \
    "llama-index-readers-file>=0.2" \
    "qdrant-client>=1.9" \
    "python-multipart>=0.0.9" \
    "httpx>=0.27" \
    "psycopg2-binary>=2.9" \
    "apscheduler>=3.10,<4"

COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
