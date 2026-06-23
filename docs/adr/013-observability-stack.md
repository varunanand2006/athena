# ADR 013 — Observability stack: hand-rolled metrics-first

**Date:** 2026-06-23
**Status:** Accepted
**Relates to:** Phase 22; ADR builds on Phase 10 (health aggregation)

---

## Context

Athena has eleven services and a background reflection/feed pipeline, but the
only runtime window into it is the Phase 10 `/system/health` aggregator — a
synchronous, point-in-time reachability + Postgres-snapshot check. It answers
"is it up right now?" but not "how slow", "how often", or "how much", and it
makes whole classes of failure invisible: the empty-`analysis_text` leetcode
rows, the poller silently stuck on the wrong Ollama API path, an empty RAG
summary that still marks a doc `complete`. Phase 22 adds real observability.
Four decisions shape it.

---

## Decision 1 — Hand-rolled slim stack, not `kube-prometheus-stack`

The default industry move is the `kube-prometheus-stack` Helm chart (Prometheus
Operator + Grafana + node-exporter + kube-state-metrics + Alertmanager + a pile
of CRDs and default alert rules). We **reject** it here and run a single
Prometheus + single Grafana from raw YAML.

- **RAM.** The control plane is 8GB; the full operator stack with all exporters
  and Alertmanager is a heavy resident set for a 3-node bare-metal cluster whose
  *job* is to run the actual product. A single Prometheus + Grafana at this
  cardinality is a few hundred MB, pinned to vlinux2 (16GB).
- **Repo convention.** CLAUDE.md: raw YAML, no Helm unless the upstream repo
  requires it. Nothing here requires it.
- **Signal.** Hand-rolling the scrape config and provisioning teaches (and
  demonstrates) how Prometheus service discovery and Grafana provisioning
  actually work — a stronger competence signal than `helm install`.

**Alternative considered:** the Helm chart, then scale pieces to zero. Rejected —
fighting a chart's defaults down to a slim footprint is more work and less
legible than ~6 small YAML files we own.

---

## Decision 2 — Annotation-based pod service discovery, not ServiceMonitors

Because we run **no Prometheus Operator**, the `ServiceMonitor` / `PodMonitor`
CRDs don't exist. Prometheus discovers targets with `kubernetes_sd_configs`
(`role: pod`) and relabels on the conventional `prometheus.io/scrape`,
`prometheus.io/port`, `prometheus.io/path` pod annotations.

- It needs only a scoped `ClusterRole` (`get/list/watch` on pods/services/
  endpoints) — no CRDs, no operator reconcile loop.
- Adding a service to monitoring becomes a **two-line annotation** on its
  existing deployment, not a new CRD object — fitting the "thin, additive
  change" grain the rest of the repo favors (cf. the MCP tool-registry pattern).
- `role: pod` scrapes the pod IP directly, so headless services need no `Service`
  object just to be scraped.

---

## Decision 3 — Metrics first; structured logs to stdout; Loki/tracing/alerting deferred

Phase 22 ships **metrics + structured JSON logs only**. Loki (log aggregation),
OpenTelemetry (tracing), and Alertmanager (paging) are explicitly out.

- **Bounded RAM + scope.** Each deferred piece is another resident workload and
  another integration surface. Metrics alone answer the latency/rate/cost/
  failure questions that motivate the phase; ship that, prove the footprint,
  then add Loki on evidence.
- **Logs still improve now.** Switching to JSON-on-stdout makes the known
  silent-failure sites emit a `level=warning` line naming the offending field —
  greppable via `kubectl logs | jq` without any aggregation tier. The format is
  Loki-ready, so adopting Loki later is config, not a rewrite.
- **Metrics are the load-bearing signal**; logs are the corroborating detail.
  Doing metrics well beats doing four tiers shallowly.

**Alternative considered:** full stack (metrics + logs + traces + alerts) in one
phase. Rejected — too much resident RAM and integration risk at once on this
hardware, and it would stall a shippable win behind three harder ones.

---

## Decision 4 — Pull-model scraping for the headless schedulers, not a push gateway

`internship` and `leetcode` are `BlockingScheduler` services with **no HTTP
server**. To scrape them we add a `prometheus_client.start_http_server(
METRICS_PORT)` listener in a daemon thread, started once on startup before the
run-once-then-schedule handoff.

- This keeps **one uniform pull model** across all four services — Prometheus
  scrapes everyone the same way; no second ingestion path to reason about.
- **Alternative considered:** a Prometheus *Pushgateway* (the usual answer for
  batch/cron jobs that don't stay up to be scraped). Rejected — these schedulers
  are long-lived processes that merely *lack* an HTTP port, not ephemeral cron
  jobs; a 30-line metrics listener is simpler than running and reasoning about a
  pushgateway's last-value-sticks semantics, and avoids a stale-metric footgun.

---

## Consequences

**Positive**
- Latency, request rate, LLM token spend, job durations, and failure/empty-result
  counts become first-class time series with committed Grafana dashboards.
- The documented silent-failure classes become loud: a dedicated
  `athena_job_empty_result_total` + a warning log line at each site.
- One uniform pull-based scrape model; adding a service to monitoring is a
  two-line annotation. Footprint stays small (single Prometheus + Grafana on
  vlinux2, ~15d retention, low-cardinality labels).

**Negative / trade-offs**
- No alerting yet — a bad dashboard only helps when someone looks.
- No aggregated log search until Loki; per-pod `kubectl logs` only.
- App metrics only — cluster/node health still comes from `kubectl` until
  exporters land.
- `prometheus-client` must be kept in sync across each service's `pyproject.toml`
  **and** `Dockerfile` (the langchain-openai sync trap), ×4 services.
- Cardinality discipline is manual — label sets are chosen low-cardinality on
  purpose; new metrics must be re-checked so the TSDB stays small.

---

## Related

- [Phase 10](../phases/phase-10-reliability-health.md) — the `/system/health`
  aggregator this complements (point-in-time check ↔ continuous time series).
- [Phase 22 write-up](../phases/phase-22-observability.md).
- Deferred follow-ons: Loki (log aggregation), OpenTelemetry (tracing),
  Alertmanager (alerting), cluster exporters (node-exporter / kube-state-metrics).
