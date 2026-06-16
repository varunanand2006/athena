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
  }
}

export default function SystemView() {
  const [health, setHealth] = useState<SystemHealth | null>(null)
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

  useEffect(() => {
    fetchHealth()
    const id = setInterval(fetchHealth, 15_000)
    return () => clearInterval(id)
  }, [fetchHealth])

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
      </div>
    </section>
  )
}

function Card({ title, value, children }: { title: string; value: number; children?: React.ReactNode }) {
  return (
    <div
      className="rounded-2xl p-5 flex flex-col gap-2 glass-panel"
      style={{ boxShadow: 'var(--shadow-md)', border: '1px solid var(--border)' }}
    >
      <p className="text-xs uppercase tracking-wider font-medium" style={{ color: 'var(--text-muted)' }}>
        {title}
      </p>
      <p className="text-2xl font-semibold" style={{ color: 'var(--text)' }}>
        {value.toLocaleString()}
      </p>
      {children}
    </div>
  )
}
