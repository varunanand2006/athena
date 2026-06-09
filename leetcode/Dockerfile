FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "apscheduler>=3.10" \
    "httpx>=0.27" \
    "psycopg2-binary>=2.9"

COPY main.py .

CMD ["python", "main.py"]
