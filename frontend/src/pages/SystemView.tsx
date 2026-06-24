import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { relativeTime } from '../utils/time'

interface ServiceCheck {
  name: string
  reachable: boolean
  latency_ms: number | null
}

interface SystemHealth {
  services: ServiceCheck[]
  data: {
    documents: { total: number; by_status: Record<string, number> }
    internships: { total: number; last_found_date: string | null }
    leetcode: { total_solved: number; last_solved_at: string | null }
    memory?: {
      note_count: number
      context_tokens: number
      max_tokens: number
      over_cap: boolean
    }
  }
}

interface SystemMetrics {
  available: boolean
  llm: {
    p95_latency_ms: number | null
    tokens_24h: number
    errors_1h: number
  }
  jobs: {
    failures_24h: { total: number; by_job: Record<string, number> }
    empty_24h: { total: number; by_job: Record<string, number> }
  }
  rag: { empty_rate_6h: number | null }
}

export default function SystemView() {
  const [health, setHealth] = useState<SystemHealth | null>(null)
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(true)

  const fetchHealth = useCallback(async () => {
    try {
      const res = await axios.get<SystemHealth>('/system/health', { timeout: 8_000 })
      setHealth(res.data)
      setError(false)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  // Metrics come from Prometheus via the agent. They degrade independently of
  // health: if monitoring isn't reachable the section just hides, and a failed
  // fetch never trips the page-level error state.
  const fetchMetrics = useCallback(async () => {
    try {
      const res = await axios.get<SystemMetrics>('/system/metrics', { timeout: 8_000 })
      setMetrics(res.data)
    } catch {
      setMetrics(null)
    }
  }, [])

  useEffect(() => {
    fetchHealth()
    fetchMetrics()
    const id = setInterval(() => {
      fetchHealth()
      fetchMetrics()
    }, 15_000)
    return () => clearInterval(id)
  }, [fetchHealth, fetchMetrics])

  return (
    <div className="h-full overflow-y-auto bg-transparent pt-8">
      <div className="px-8 pb-8 flex flex-col gap-6 z-10">
        {loading && !health && (
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>Loading…</p>
        )}
        {error && (
          <p className="text-sm text-red-500">Failed to load system health</p>
        )}

        {health && (
          <>
            <ServicesSection services={health.services} />
            <DataSection data={health.data} />
            {metrics?.available && <MetricsSection metrics={metrics} />}
          </>
        )}
      </div>
    </div>
  )
}

function ServicesSection({ services }: { services: ServiceCheck[] }) {
  return (
    <section
      className="rounded-2xl overflow-hidden glass-card"
      style={{ border: '1px solid var(--border)' }}
    >
      <div className="px-5 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <h2 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>Services</h2>
      </div>
      <ul>
        {services.map((s) => (
          <li
            key={s.name}
            className="px-5 py-3 flex items-center justify-between"
            style={{ borderTop: '1px solid var(--border)' }}
          >
            <div className="flex items-center gap-2.5">
              <span
                className="inline-block w-2 h-2 rounded-full shrink-0"
                style={{
                  background: s.reachable ? '#4ADE80' : '#F87171',
                  boxShadow: `0 0 6px ${(s.reachable ? '#4ADE80' : '#F87171')}80`,
                }}
              />
              <span className="text-sm font-medium" style={{ color: 'var(--text)' }}>
                {s.name}
              </span>
            </div>
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {s.latency_ms === null ? 'unreachable' : `${s.latency_ms} ms`}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function DataSection({ data }: { data: SystemHealth['data'] }) {
  const docs = data.documents
  const complete = docs.by_status.complete ?? 0
  const processing = docs.by_status.processing ?? 0
  const failed = docs.by_status.failed ?? 0

  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-semibold text-sm px-1" style={{ color: 'var(--text)' }}>Data</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Card title="Documents" value={docs.total}>
          <div className="text-xs flex gap-3" style={{ color: 'var(--text-muted)' }}>
            <span>complete: {complete}</span>
            <span>processing: {processing}</span>
            <span style={{ color: failed > 0 ? 'var(--accent)' : 'var(--text-muted)' }}>
              failed: {failed}
            </span>
          </div>
        </Card>

        <Card title="Internships found" value={data.internships.total}>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            last poll: {relativeTime(data.internships.last_found_date)}
          </p>
        </Card>

        <Card title="LeetCode solved" value={data.leetcode.total_solved}>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            last solved: {relativeTime(data.leetcode.last_solved_at)}
          </p>
        </Card>

        {data.memory && (
          <Card title="Memory notes" value={data.memory.note_count}>
            <div className="text-xs flex flex-col gap-0.5" style={{ color: 'var(--text-muted)' }}>
              <span
                style={{ color: data.memory.over_cap ? 'var(--accent)' : 'var(--text-muted)' }}
              >
                recall context: ~{data.memory.context_tokens.toLocaleString()} / {data.memory.max_tokens.toLocaleString()} tok
              </span>
              {data.memory.over_cap && (
                <span style={{ color: 'var(--accent)' }}>
                  ⚠ over cap — vault too big for full-context load (time for embeddings)
                </span>
              )}
            </div>
          </Card>
        )}
      </div>
    </section>
  )
}

function MetricsSection({ metrics }: { metrics: SystemMetrics }) {
  const { llm, jobs, rag } = metrics
  const p95 = llm.p95_latency_ms
  const emptyTotal = jobs.empty_24h.total
  const failTotal = jobs.failures_24h.total
  const ragPct = rag.empty_rate_6h === null ? null : Math.round(rag.empty_rate_6h * 100)

  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-semibold text-sm px-1" style={{ color: 'var(--text)' }}>Observability</h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Card title="LLM p95 latency" value={p95 ?? 0} unit={p95 === null ? '— no data' : 'ms (5m)'}>
          <div className="text-xs flex gap-3" style={{ color: 'var(--text-muted)' }}>
            <span>tokens 24h: {llm.tokens_24h.toLocaleString()}</span>
            <span style={{ color: llm.errors_1h > 0 ? 'var(--accent)' : 'var(--text-muted)' }}>
              errors 1h: {llm.errors_1h}
            </span>
          </div>
        </Card>

        <Card
          title="Silent failures 24h"
          value={emptyTotal}
          unit="empty results"
          accent={emptyTotal > 0}
        >
          <JobBreakdown by={jobs.empty_24h.by_job} empty="no empty-result jobs" />
        </Card>

        <Card title="Job failures 24h" value={failTotal} accent={failTotal > 0}>
          <JobBreakdown by={jobs.failures_24h.by_job} empty="no job failures" />
        </Card>

        <Card
          title="RAG empty-rate"
          value={ragPct ?? 0}
          unit={ragPct === null ? '— no lookups' : '% (6h)'}
          accent={ragPct !== null && ragPct >= 50}
        >
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            find_documents returning nothing
          </p>
        </Card>
      </div>
    </section>
  )
}

function JobBreakdown({ by, empty }: { by: Record<string, number>; empty: string }) {
  const entries = Object.entries(by).filter(([, v]) => v > 0)
  if (entries.length === 0) {
    return <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{empty}</p>
  }
  return (
    <div className="text-xs flex flex-wrap gap-x-3 gap-y-0.5" style={{ color: 'var(--accent)' }}>
      {entries.map(([job, count]) => (
        <span key={job}>{job}: {count}</span>
      ))}
    </div>
  )
}

function Card({ title, value, children, unit, accent }: { title: string; value: number; children?: React.ReactNode; unit?: string; accent?: boolean }) {
  return (
    <div
      className="rounded-2xl p-5 flex flex-col gap-2 glass-panel"
      style={{ boxShadow: 'var(--shadow-md)', border: '1px solid var(--border)' }}
    >
      <p className="text-xs uppercase tracking-wider font-medium" style={{ color: 'var(--text-muted)' }}>
        {title}
      </p>
      <p className="text-2xl font-semibold flex items-baseline gap-1.5" style={{ color: accent ? 'var(--accent)' : 'var(--text)' }}>
        {value.toLocaleString()}
        {unit && <span className="text-xs font-normal" style={{ color: 'var(--text-muted)' }}>{unit}</span>}
      </p>
      {children}
    </div>
  )
}
