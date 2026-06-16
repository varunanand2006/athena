import { useEffect, useState } from 'react'
import axios from 'axios'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import type { Message } from '../App'

// ---- Types ----------------------------------------------------------------

interface Internship {
  id: number
  company: string
  role: string
  location: string
  priority_score: number
  resume_recommendation: string
  apply_link: string | null
  found_date: string
}

interface LeetCodeStats {
  total: number
  easy: number
  medium: number
  hard: number
  last_solved_date: string | null
}

// ---- Shared card shell ----------------------------------------------------

function Card({ title, badge, children }: { title: string; badge?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div
      className="rounded-2xl p-5 flex flex-col gap-3 glass-card"
      style={{ boxShadow: 'var(--shadow-md)', border: '1px solid var(--border)' }}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>
          {title}
        </h2>
        {badge}
      </div>
      {children}
    </div>
  )
}

function Pill({ children, color }: { children: React.ReactNode; color: 'indigo' | 'green' }) {
  const styles =
    color === 'indigo'
      ? { background: 'var(--accent-light)', color: 'var(--accent)' }
      : { background: '#F0FDF4', color: '#16A34A' }
  return (
    <span className="text-xs font-medium px-2 py-0.5 rounded-full" style={styles}>
      {children}
    </span>
  )
}

// ---- Score badge ----------------------------------------------------------

function ScoreBadge({ score }: { score: number }) {
  const color = score >= 8 ? '#16A34A' : score >= 5 ? '#D97706' : '#DC2626'
  const bg    = score >= 8 ? '#F0FDF4'  : score >= 5 ? '#FFFBEB'  : '#FEF2F2'
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold shrink-0"
      style={{ color, background: bg, border: `1px solid ${color}30` }}
    >
      {score.toFixed(1)}
    </span>
  )
}

// ---- Internship card ------------------------------------------------------

function InternshipCard() {
  const [data, setData] = useState<Internship[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    axios
      .get<Internship[]>('/internships', { timeout: 10_000 })
      .then((r) => setData(r.data))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  return (
    <Card
      title="Internship Pipeline"
      badge={
        !loading && !error ? (
          <Pill color="indigo">{data.length} today</Pill>
        ) : undefined
      }
    >
      {loading && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--text-muted)' }}>
          Loading…
        </p>
      )}
      {error && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--accent)' }}>Failed to load internships</p>
      )}
      {!loading && !error && data.length === 0 && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--text-muted)' }}>
          No new postings today
        </p>
      )}
      {!loading && !error && data.length > 0 && (
        <div className="overflow-y-auto max-h-72 flex flex-col gap-2 pr-0.5">
          {data.map((item) => (
            <div
              key={item.id}
              className="p-3 rounded-xl flex flex-col gap-1.5 glass-card"
              style={{ border: '1px solid var(--border)' }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-medium text-sm truncate" style={{ color: 'var(--text)' }}>
                    {item.company}
                  </p>
                  <p className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                    {item.role}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <ScoreBadge score={item.priority_score} />
                  {item.apply_link && (
                    <a
                      href={item.apply_link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-medium px-2.5 py-1 rounded-lg text-white shrink-0"
                      style={{ background: 'var(--accent)' }}
                    >
                      Apply
                    </a>
                  )}
                </div>
              </div>
              {item.resume_recommendation && (
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  Resume: <span className="font-medium">{item.resume_recommendation}</span>
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

// ---- LeetCode card --------------------------------------------------------

const CHART_DATA_TEMPLATE = [
  { name: 'Easy',   key: 'easy'   as const, color: '#16A34A' },
  { name: 'Medium', key: 'medium' as const, color: '#D97706' },
  { name: 'Hard',   key: 'hard'   as const, color: '#DC2626' },
]

function LeetCodeCard() {
  const [data, setData] = useState<LeetCodeStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    axios
      .get<LeetCodeStats>('/leetcode', { timeout: 10_000 })
      .then((r) => setData(r.data))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  const chartData = data
    ? CHART_DATA_TEMPLATE.map((t) => ({ name: t.name, count: data[t.key], color: t.color }))
    : []

  return (
    <Card
      title="LeetCode Stats"
      badge={data ? <Pill color="indigo">{data.total} solved</Pill> : undefined}
    >
      {loading && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--text-muted)' }}>
          Loading…
        </p>
      )}
      {error && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--accent)' }}>Failed to load LeetCode stats</p>
      )}
      {!loading && !error && data && (
        <>
          <div className="h-36">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
                <XAxis dataKey="name" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                <Tooltip
                  contentStyle={{
                    fontSize: 12,
                    borderRadius: 8,
                    border: '1px solid var(--border)',
                    boxShadow: 'var(--shadow)',
                  }}
                  cursor={{ fill: 'var(--bg)' }}
                  formatter={(v: number) => [v, 'Solved']}
                />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {chartData.map((entry, i) => (
                    <Cell key={i} fill={entry.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div
            className="flex items-center justify-between pt-2"
            style={{ borderTop: '1px solid var(--border)' }}
          >
            <div className="flex gap-3 text-xs font-medium">
              <span className="text-green-600">{data.easy}E</span>
              <span className="text-amber-600">{data.medium}M</span>
              <span className="text-red-600">{data.hard}H</span>
            </div>
            {data.last_solved_date && (
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Last: {new Date(data.last_solved_date).toLocaleDateString()}
              </span>
            )}
          </div>
        </>
      )}
    </Card>
  )
}

// ---- Documents card ---------------------------------------------------------

interface Document {
  id: string
  filename: string
  title: string
  doc_type: string
  summary: string
  chunk_count: number
  size_bytes: number
  status: 'processing' | 'complete' | 'failed'
  added_at: string | null
}

function DocumentsCard() {
  const [data, setData] = useState<Document[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    axios
      .get<Document[]>('/documents', { timeout: 10_000 })
      .then((r) => setData(r.data))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  return (
    <Card
      title="Recent Documents"
      badge={
        !loading && !error ? (
          <Pill color="indigo">{data.length} total</Pill>
        ) : undefined
      }
    >
      {loading && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--text-muted)' }}>
          Loading…
        </p>
      )}
      {error && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--accent)' }}>Failed to load documents</p>
      )}
      {!loading && !error && data.length === 0 && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--text-muted)' }}>
          No documents uploaded yet
        </p>
      )}
      {!loading && !error && data.length > 0 && (
        <div className="overflow-y-auto max-h-72 flex flex-col gap-2 pr-0.5">
          {data.slice(0, 5).map((item) => (
            <div
              key={item.id}
              className="p-3 rounded-xl flex flex-col gap-1.5 glass-card"
              style={{ border: '1px solid var(--border)' }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-medium text-sm truncate" style={{ color: 'var(--text)' }}>
                    {item.title}
                  </p>
                  <p className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                    {item.filename} • {item.doc_type}
                  </p>
                </div>
                {item.status === 'processing' && (
                  <span className="text-xs font-medium px-2 py-0.5 rounded-full" style={{ background: '#DBEAFE', color: '#1D4ED8' }}>
                    Processing
                  </span>
                )}
                {item.status === 'failed' && (
                  <span className="text-xs font-medium text-red-500">Failed</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

// ---- Memory card ----------------------------------------------------------

interface NoteMeta {
  slug: string
  title: string
  tags: string[]
  created: string
  updated: string
  source: string
}

function MemoryCard() {
  const [data, setData] = useState<NoteMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    axios
      .get<NoteMeta[]>('/memory', { timeout: 10_000 })
      .then((r) => setData(r.data))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  return (
    <Card
      title="Memory Vault"
      badge={
        !loading && !error ? (
          <Pill color="green">{data.length} notes</Pill>
        ) : undefined
      }
    >
      {loading && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--text-muted)' }}>
          Loading…
        </p>
      )}
      {error && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--accent)' }}>Failed to load memory vault</p>
      )}
      {!loading && !error && data.length === 0 && (
        <p className="text-sm py-4 text-center" style={{ color: 'var(--text-muted)' }}>
          No notes stored
        </p>
      )}
      {!loading && !error && data.length > 0 && (
        <div className="overflow-y-auto max-h-72 flex flex-col gap-2 pr-0.5">
          {data.slice(0, 5).map((item) => (
            <div
              key={item.slug}
              className="p-3 rounded-xl flex flex-col gap-1.5 glass-card"
              style={{ border: '1px solid var(--border)' }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-medium text-sm truncate" style={{ color: 'var(--text)' }}>
                    {item.title}
                  </p>
                  {item.tags && item.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {item.tags.slice(0, 3).map((t) => (
                        <span
                          key={t}
                          className="text-[10px] px-1.5 py-0.5 rounded-sm"
                          style={{ background: 'var(--accent-light)', color: 'var(--accent)' }}
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <span className="text-xs shrink-0" style={{ color: 'var(--text-muted)' }}>
                  {new Date(item.updated).toLocaleDateString()}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

// ---- Dashboard layout -----------------------------------------------------

export default function DashboardView() {
  return (
    <div className="h-full overflow-y-auto bg-transparent pt-8">
      <div className="px-8 pb-8 grid grid-cols-1 lg:grid-cols-2 gap-6 items-start z-10">
        <InternshipCard />
        <LeetCodeCard />
        <DocumentsCard />
        <MemoryCard />
      </div>
    </div>
  )
}
