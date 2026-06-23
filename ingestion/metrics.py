"""Phase 22 — Prometheus metrics + structured JSON logging.

Self-contained, dependency-light observability helpers. The four instrumented
services (`agent`, `ingestion`, `internship`, `leetcode`) share no Python
package, so this module is COPIED verbatim into each service directory and must
be kept in sync by hand — the same discipline as the `prometheus-client` /
`langchain-openai` Dockerfile sync trap (see docs/claude/key-lessons.md).

Design notes
------------
* One `athena_`-prefixed metric set on the default registry. Every series carries
  a `service` label sourced from the ``SERVICE_NAME`` env var, plus deliberately
  LOW-CARDINALITY extra labels (model / operation / job / route) — never per-user,
  per-document, or per-conversation values. Re-check cardinality when adding a
  metric; the TSDB stays small only because the label sets stay small.
* FastAPI services (`agent`, `ingestion`) call :func:`instrument_fastapi` to mount
  ``/metrics`` and a latency middleware. Headless schedulers (`internship`,
  `leetcode`) call :func:`start_metrics_server` to expose ``/metrics`` from a
  daemon-thread HTTP listener (ADR 013, decision 4 — one uniform pull model).
* :func:`configure_logging` switches stdlib logging to JSON-lines-on-stdout so the
  known silent-failure sites can emit a greppable ``level=warning`` line naming
  the offending field (``kubectl logs | jq`` is the v1 query path; no Loki yet).
"""

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
    start_http_server as _prom_start_http_server,
)

SERVICE = os.getenv("SERVICE_NAME", "unknown")

# --- Metric definitions (athena_ prefix, low-cardinality labels) ------------

# LLM calls — latency, token spend, errors. `operation` is a small fixed set
# (chat / summary / reflection / analysis / company_research / scoring).
LLM_REQUEST_SECONDS = Histogram(
    "athena_llm_request_seconds",
    "LLM request duration in seconds",
    ["service", "model", "operation"],
)
LLM_TOKENS_TOTAL = Counter(
    "athena_llm_tokens_total",
    "LLM tokens consumed, read from the provider usage block",
    ["service", "model", "kind"],  # kind = prompt | completion
)
LLM_ERRORS_TOTAL = Counter(
    "athena_llm_errors_total",
    "LLM request failures (transport or provider error)",
    ["service", "model", "operation"],
)

# Background jobs — duration, hard failures, and the silent-failure signal: a
# job that completed "successfully" but produced an empty artifact.
JOB_SECONDS = Histogram(
    "athena_job_seconds",
    "Background job duration in seconds",
    ["service", "job"],
)
JOB_FAILURES_TOTAL = Counter(
    "athena_job_failures_total",
    "Background job hard failures",
    ["service", "job"],
)
JOB_EMPTY_RESULT_TOTAL = Counter(
    "athena_job_empty_result_total",
    "Jobs that completed but produced an empty/blank artifact",
    ["service", "job"],
)

# RAG retrieval-quality smoke signal.
RAG_LOOKUPS_TOTAL = Counter(
    "athena_rag_lookups_total",
    "find_documents lookups",
    ["service"],
)
RAG_EMPTY_TOTAL = Counter(
    "athena_rag_empty_total",
    "find_documents lookups that returned nothing",
    ["service"],
)

# HTTP (FastAPI services) — latency + count by templated route + status.
HTTP_REQUEST_SECONDS = Histogram(
    "athena_http_request_seconds",
    "HTTP request duration in seconds",
    ["service", "method", "route", "status"],
)


# --- LLM helpers ------------------------------------------------------------


@contextmanager
def track_llm(model: str, operation: str):
    """Time an LLM call and count errors. Wrap the request body::

        with metrics.track_llm(model, "chat"):
            resp = client.post(...)

    On exception, increments ``athena_llm_errors_total`` and re-raises; always
    observes ``athena_llm_request_seconds``. Token counters are recorded
    separately by :func:`record_tokens` once the response is parsed.
    """
    start = time.perf_counter()
    try:
        yield
    except Exception:
        LLM_ERRORS_TOTAL.labels(SERVICE, model, operation).inc()
        raise
    finally:
        LLM_REQUEST_SECONDS.labels(SERVICE, model, operation).observe(
            time.perf_counter() - start
        )


def record_tokens(model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """Add prompt/completion token counts (from the provider usage block)."""
    if prompt_tokens:
        LLM_TOKENS_TOTAL.labels(SERVICE, model, "prompt").inc(prompt_tokens)
    if completion_tokens:
        LLM_TOKENS_TOTAL.labels(SERVICE, model, "completion").inc(completion_tokens)


def record_openai_usage(model: str, usage: dict | None) -> None:
    """Record tokens from an OpenAI-style ``usage`` dict (prompt_tokens /
    completion_tokens). No-op if usage is missing."""
    if not usage:
        return
    record_tokens(
        model,
        prompt_tokens=usage.get("prompt_tokens", 0) or 0,
        completion_tokens=usage.get("completion_tokens", 0) or 0,
    )


def record_ollama_usage(model: str, response: dict | None) -> None:
    """Record tokens from an Ollama ``/api/chat`` response (prompt_eval_count /
    eval_count). No-op if absent."""
    if not response:
        return
    record_tokens(
        model,
        prompt_tokens=response.get("prompt_eval_count", 0) or 0,
        completion_tokens=response.get("eval_count", 0) or 0,
    )


# --- Background-job helpers -------------------------------------------------


@contextmanager
def track_job(job: str):
    """Time a background job and count hard failures::

        with metrics.track_job("reflection"):
            ...

    On exception, increments ``athena_job_failures_total`` and re-raises; always
    observes ``athena_job_seconds``. Use :func:`job_empty_result` when the job
    completes without raising but produced nothing usable.
    """
    start = time.perf_counter()
    try:
        yield
    except Exception:
        JOB_FAILURES_TOTAL.labels(SERVICE, job).inc()
        raise
    finally:
        JOB_SECONDS.labels(SERVICE, job).observe(time.perf_counter() - start)


def job_failure(job: str) -> None:
    """Increment the hard-failure counter for a job outside a ``track_job`` block."""
    JOB_FAILURES_TOTAL.labels(SERVICE, job).inc()


def job_empty_result(job: str) -> None:
    """Increment the silent-failure counter: job completed, artifact was empty."""
    JOB_EMPTY_RESULT_TOTAL.labels(SERVICE, job).inc()


# --- RAG helpers ------------------------------------------------------------


def record_rag_lookup(hit_count: int) -> None:
    """Count a find_documents lookup; flag it empty when it returned nothing."""
    RAG_LOOKUPS_TOTAL.labels(SERVICE).inc()
    if not hit_count:
        RAG_EMPTY_TOTAL.labels(SERVICE).inc()


# --- FastAPI / ASGI integration ---------------------------------------------


class _PrometheusMiddleware:
    """Pure-ASGI latency middleware. Pure ASGI (not BaseHTTPMiddleware) so it does
    not buffer the agent's SSE ``/chat/stream`` response. Labels by the *templated*
    route path (e.g. ``/conversations/{id}/messages``) to keep cardinality bounded;
    unmatched paths collapse to ``<unmatched>`` rather than leaking raw URLs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            path = getattr(route, "path", None) or "<unmatched>"
            HTTP_REQUEST_SECONDS.labels(
                SERVICE, scope.get("method", "GET"), path, str(status["code"])
            ).observe(time.perf_counter() - start)

    # /metrics must not pollute the histogram with self-scrapes; the route label
    # for it is still low-cardinality so we let it through for simplicity.


def metrics_response():
    """Return the Prometheus exposition payload as a FastAPI Response."""
    from fastapi import Response

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def instrument_fastapi(app) -> None:
    """Mount the latency middleware and a ``GET /metrics`` route on a FastAPI app."""
    app.add_middleware(_PrometheusMiddleware)
    app.add_api_route("/metrics", metrics_response, methods=["GET"], include_in_schema=False)


# --- Headless-scheduler integration -----------------------------------------


def start_metrics_server(port: int | None = None) -> None:
    """Start a daemon-thread ``/metrics`` HTTP listener for a service with no HTTP
    server of its own (the BlockingScheduler poller pattern). Call once on startup,
    before the run-once-then-schedule handoff, so the very first deploy is
    scrapeable. Port defaults to the ``METRICS_PORT`` env (fallback 9100)."""
    if port is None:
        port = int(os.getenv("METRICS_PORT", "9100"))
    _prom_start_http_server(port)
    logging.getLogger(__name__).info(
        "metrics listener started", extra={"job": "startup", "field": f"port={port}"}
    )


# --- Structured JSON logging ------------------------------------------------


class JsonLogFormatter(logging.Formatter):
    """Render each log record as a single JSON line. Promotes a fixed set of
    structured extras (``job``, ``model``, ``operation``, ``latency_ms``,
    ``field``) to top-level keys when present, so the silent-failure warning lines
    are greppable by field with ``jq`` without parsing the message string."""

    _EXTRA_KEYS = ("job", "model", "operation", "latency_ms", "field")

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "service": SERVICE,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in self._EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Switch the root logger to JSON-lines-on-stdout. Replaces any handler
    installed by ``logging.basicConfig`` so uvicorn's app loggers also emit JSON."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
