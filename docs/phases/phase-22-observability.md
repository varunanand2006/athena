# Phase 22: Observability — metrics + structured logs

**Status:** Implemented (pending cluster rollout)
**Depends on:** Phase 10 (health aggregation), and every LLM/job-running service
(agent, ingestion, internship, leetcode)

## Goal

Turn "I built services" into "I operate a system." Today the only window into
the cluster is the Phase 10 `/system/health` aggregator — a point-in-time
reachability + data-snapshot check. It cannot answer *how slow*, *how often*, or
*how much* — and it makes whole classes of failure invisible (the empty
`analysis_text` rows, the leetcode poller silently on the wrong Ollama path).
This phase adds **time-series metrics** (Prometheus + Grafana) and **structured
logs** so those questions are answerable and those failures are loud.

Scope is deliberately **metrics-first**. Log *aggregation* (Loki), distributed
tracing (OpenTelemetry), and alerting (Alertmanager) are explicitly deferred —
see "Explicitly NOT in this phase". This keeps the RAM footprint bounded on a
3-node bare-metal cluster and keeps the phase shippable.

See [ADR 013](../adr/013-observability-stack.md) for the decision record.

---

## Part 1 — Instrument the services

### The split: two FastAPI services, two headless schedulers

The four LLM/job services do **not** all speak HTTP, which dictates how each
exposes metrics:

| Service | Runtime | How it exposes `/metrics` |
|---------|---------|---------------------------|
| `agent` | FastAPI + `BackgroundScheduler` | mount a `/metrics` route on the existing app |
| `ingestion` | FastAPI + `BackgroundScheduler` | mount a `/metrics` route on the existing app |
| `internship` | `BlockingScheduler`, **no HTTP server** | `prometheus_client.start_http_server(METRICS_PORT)` in a daemon thread before `scheduler.start()` |
| `leetcode` | `BlockingScheduler`, **no HTTP server** | same daemon-thread metrics listener |

For the two headless schedulers the metrics listener is started **once on
startup**, before the existing run-once-then-schedule handoff (the Phase 5
APScheduler pattern), so the very first deploy is scrapeable.

### Dependency

`prometheus-client` is added to **both** `pyproject.toml` *and* the `Dockerfile`
pip list for each of the four services — the Dockerfiles don't read
`pyproject.toml`, so they must be kept in sync by hand (the same trap as
`langchain-openai` in the agent image; see key-lessons).

### What gets measured (v1)

Counters/histograms, named under an `athena_` prefix, labeled by `service` and
(where relevant) `model` / `operation`:

- **LLM calls** — `athena_llm_request_seconds` (histogram, labels:
  service, model, operation e.g. `chat`/`summary`/`reflection`/`analysis`),
  `athena_llm_tokens_total` (counter, labels: service, model, kind=`prompt|completion`,
  read from the OpenAI usage block), `athena_llm_errors_total`.
- **Background jobs** — `athena_job_seconds` (histogram, label: job e.g.
  `reflection`/`external_feeds`/`internship_pipeline`/`leetcode_sync`/`ingest_worker`),
  `athena_job_failures_total`. This is what surfaces the silent-failure class:
  a job that completes "successfully" but produced an empty artifact increments
  a dedicated `athena_job_empty_result_total{job=...}` counter.
- **HTTP (FastAPI services)** — request latency + count by route + status, via a
  thin ASGI middleware (no extra heavyweight dep; manual histogram).
- **RAG** — `athena_rag_lookups_total` and `athena_rag_empty_total` (a
  `find_documents` that returned nothing — the retrieval-quality smoke signal).

Business gauges that live in Postgres (document count, conversation count) are
**out of v1** — they're already one query away via `/system/health`; promoting
them to scraped gauges is a later nicety, not a launch requirement.

### Structured logs

Each service switches its stdlib logging to **JSON lines on stdout** (timestamp,
level, service, logger, message, and any structured extras — `job`, `model`,
`latency_ms`). No Loki yet: `kubectl logs` + `jq` is the v1 query path. The win
is that the silent-failure sites already flagged in key-lessons (empty summary,
empty `analysis_text`, empty `message.content`) emit a `level=warning` JSON line
with the offending field, so they're greppable instead of archaeological.

---

## Part 2 — The monitoring stack (slim, hand-rolled, no Helm)

A single Prometheus + single Grafana, raw YAML, pinned to **vlinux2** (16GB,
`workload=services`) — **not** on the 8GB control plane, and **not** the
`kube-prometheus-stack` Helm chart (too heavy for this cluster, and the repo
convention is raw YAML). New manifests under `cluster/monitoring/`.

### Namespace + RBAC

A dedicated `monitoring` namespace. Prometheus needs a `ServiceAccount` + a
`ClusterRole` granting `get/list/watch` on `pods`, `services`, `endpoints`
(cluster-scoped because it discovers pods in the `athena` namespace), bound via
`ClusterRoleBinding`.

### Scraping: annotation-based pod service discovery

No Prometheus Operator is installed, so there are **no `ServiceMonitor` CRDs**.
Prometheus uses `kubernetes_sd_configs` with `role: pod` and relabels on
standard pod annotations. Each service's pod template (in its existing
`cluster/<svc>/deployment.yaml`) gains:

```yaml
metadata:
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "<metrics port>"   # app port for FastAPI svc; METRICS_PORT for schedulers
    prometheus.io/path: "/metrics"
```

`role: pod` SD scrapes the pod IP directly, so the headless scheduler services
need **no `Service` object** for scraping — just the annotation + an exposed
`containerPort`.

### Prometheus

- `Deployment` (1 replica), config from a `ConfigMap` (`prometheus.yml` with the
  pod-SD scrape job + relabeling).
- `PVC` for the TSDB, `local-path`, pinned to vlinux2 (the storage-pinning
  lesson — co-locate PVC and pod via `nodeSelector`). Retention ~15d
  (`--storage.tsdb.retention.time=15d`) — small at this cardinality.
- Modest resource requests/limits (request ~256Mi, limit ~512Mi is comfortable
  at this scale).
- `Service` (ClusterIP) so Grafana can reach it.

### Grafana

- `Deployment` (1 replica) + `PVC` (local-path, vlinux2) for its SQLite state.
- **Provisioned** datasource (Prometheus, set as default) and dashboards via
  `ConfigMap`s mounted into the provisioning dirs — so the dashboards are
  **code in the repo**, not click-ops that vanish on a pod restart.
- Admin password from a `grafana-secret` in the `monitoring` namespace
  (`-n monitoring` — the namespace gotcha from key-lessons applies here too).
- Exposed via Traefik ingress at `grafana.local` (→ 192.168.96.200, the control
  plane where Traefik runs, same as `athena.local`).

### Dashboards (the payoff artifact)

Committed JSON dashboards: **Athena Overview** (per-service request rate +
p50/p95 latency, LLM latency + token spend over time, job durations + failure
counts, RAG empty-rate). This is the screenshot that backs the resume bullet.

### In-frontend `/system/metrics` (follow-on)

Grafana is the deep-dive surface, but a glanceable subset is also folded into the
app's own `/system` view (the Phase 10 health page), so the operator sees LLM
latency/token spend, job failures, the silent-failure count, and RAG empty-rate
without leaving Athena. A `GET /system/metrics` endpoint on the agent runs a
handful of instant PromQL queries against Prometheus (`prometheus.monitoring.svc`,
ClusterIP — PromQL never reaches the browser), mirroring the `/system/health`
fan-out discipline (parallel, short per-query timeout). It **degrades
gracefully** — returns `{"available": false}` and the frontend hides the section
when monitoring is unreachable, so the health view never depends on the
monitoring stack being up. NaN/±Inf samples (idle `histogram_quantile`, 0/0
ratios) are mapped to `null` and render as "— no data". The nginx `^/(…|system|…)`
proxy prefix already routes the new sub-path to the agent — no proxy change.

---

## Gate

1. **Instrumentation** — each of the four services serves `/metrics` exposing
   `athena_*` series; FastAPI services on the app port, schedulers on
   `METRICS_PORT`.
2. **Discovery** — Prometheus *Targets* page shows all four services + itself
   `UP` (annotation SD working).
3. **Dashboards** — Grafana Athena Overview renders real data: an LLM call moves
   `athena_llm_request_seconds`, a chat turn moves token counters, a reflection
   run moves `athena_job_seconds{job="reflection"}`.
4. **Silent-failure visibility** — force an empty-result path (e.g. point a
   service at a dead dependency); confirm `athena_job_failures_total` /
   `athena_job_empty_result_total` increments **and** a `level=warning` JSON log
   line names the offending field.

---

## Explicitly NOT in this phase

- **Log aggregation (Loki / Promtail)** — structured JSON to stdout only;
  `kubectl logs` is the v1 query path. Loki is a clean follow-on once metrics are
  proven and the RAM budget is understood.
- **Distributed tracing (OpenTelemetry)** — the agent → tool → ingestion call
  chain trace is a separate later phase.
- **Alerting (Alertmanager) / paging** — dashboards are read manually in v1.
- **Cluster-level exporters** (`node-exporter`, `kube-state-metrics`) — app
  metrics first; node/k8s metrics are additive later and not the resume point.
- **Helm / the Prometheus Operator** — raw YAML, hand-rolled, per repo convention.

## Deployment

This phase rebuilds **all four** service images (heavier than the usual
single-agent rollout) plus applies the new `cluster/monitoring/` manifests.
Standard per-service image workflow (build on xdev-sr, `k3s ctr` import on the
node where the pod runs, bump the YAML tag to `:phase22`):

```
# on xdev-sr, per service dir (agent shown):
sudo docker build -t athena-agent:phase22 .
sudo docker save -o /tmp/athena-agent.tar athena-agent:phase22   # no gzip
sudo chmod 644 /tmp/athena-agent.tar
sudo k3s ctr images import /tmp/athena-agent.tar                 # k3s ctr, NOT plain ctr
# agent + ingestion images run on xdev-sr → import there directly;
# internship + leetcode run on vlinux2 → reverse-SCP the tar to vlinux2, import there.

# from vlinux1 or laptop (vlinux2 has no kubeconfig):
kubectl create namespace monitoring
kubectl create secret generic grafana-secret -n monitoring \
  --from-literal=admin-password='...'                          # -n monitoring, not default
kubectl apply -f cluster/monitoring/                            # RBAC, prometheus, grafana, ingress
kubectl apply -n athena -f cluster/agent/deployment.yaml        # + scrape annotations, :phase22
# ...repeat apply for ingestion / internship / leetcode
kubectl rollout restart -n athena deployment/agent              # etc.
```

## Known limitations & future work

- **No alerting** — a red dashboard only helps if someone is looking; Alertmanager
  is the natural next step.
- **No log search** — JSON-on-stdout is greppable per-pod but not aggregated;
  Loki closes that.
- **App metrics only** — node/cluster health (CPU, memory pressure, pod restarts
  cluster-wide) still comes from `kubectl`, not a dashboard, until exporters land.
- **Cardinality discipline is manual** — the `athena_*` label sets are chosen to
  stay low-cardinality (no per-conversation / per-document labels); re-check when
  adding metrics so the TSDB stays small.

## Docs

- [ADR 013](../adr/013-observability-stack.md) — observability-stack design
- Builds on [Phase 10](phase-10-reliability-health.md) (health aggregation)
- Next in the agreed sequence: Phase 23 (LLM eval harness), Phase 24 (CI/CD + GitOps)
