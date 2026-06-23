import { useEffect, useState } from 'react'
import axios from 'axios'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  PieChart, Pie, Sector,
} from 'recharts'

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

interface TopicCount {
  topic: string
  count: number
}

interface RecentProblem {
  title: string
  slug: string
  difficulty: string
  topics: string[]
  solved_at: string | null
}

interface LeetCodeStats {
  total: number
  easy: number
  medium: number
  hard: number
  last_solved_date: string | null
  topics: TopicCount[]
  recent: RecentProblem[]
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

// Palette for topic pie slices; cycles if there are more slices than colors.
const TOPIC_COLORS = [
  '#6366F1', '#0EA5E9', '#10B981', '#F59E0B', '#EF4444',
  '#8B5CF6', '#EC4899', '#14B8A6', '#F97316', '#3B82F6',
  '#A3E635', '#E11D48',
]
const OTHER_COLOR = '#CBD5E1'

// How many topics get their own slice before the long tail folds into "Other".
const MAX_TOPIC_SLICES = 12

type LeetView = 'topics' | 'difficulty' | 'recent'

interface PieSlice extends TopicCount {
  color: string
}

function difficultyColor(d: string): string {
  const k = d.toLowerCase()
  if (k === 'easy') return '#16A34A'
  if (k === 'medium') return '#D97706'
  if (k === 'hard') return '#DC2626'
  return 'var(--text-muted)'
}

// Top N topics as individual slices; the long tail folds into a single "Other".
function buildTopicPie(topics: TopicCount[], maxSlices = MAX_TOPIC_SLICES): PieSlice[] {
  const head = topics.slice(0, maxSlices).map((t, i) => ({
    ...t,
    color: TOPIC_COLORS[i % TOPIC_COLORS.length],
  }))
  const tail = topics.slice(maxSlices)
  if (tail.length > 0) {
    head.push({
      topic: `Other (${tail.length})`,
      count: tail.reduce((sum, t) => sum + t.count, 0),
      color: OTHER_COLOR,
    })
  }
  return head
}

// Active pie slice: pull it out slightly and label the donut centre with the
// selected topic + its count and share.
function renderActiveTopic(props: any) {
  const { cx, cy, innerRadius, outerRadius, startAngle, endAngle, fill, payload, percent } = props
  return (
    <g>
      <text x={cx} y={cy - 6} textAnchor="middle" style={{ fontSize: 12, fontWeight: 700, fill: 'var(--text)' }}>
        {payload.topic}
      </text>
      <text x={cx} y={cy + 12} textAnchor="middle" style={{ fontSize: 11, fill: 'var(--text-muted)' }}>
        {payload.count} · {Math.round((percent || 0) * 100)}%
      </text>
      <Sector
        cx={cx}
        cy={cy}
        innerRadius={innerRadius}
        outerRadius={outerRadius + 5}
        startAngle={startAngle}
        endAngle={endAngle}
        fill={fill}
      />
    </g>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="text-xs font-medium px-2.5 py-1 rounded-lg transition"
      style={
        active
          ? { background: 'var(--accent-light)', color: 'var(--accent)' }
          : { background: 'transparent', color: 'var(--text-muted)' }
      }
    >
      {children}
    </button>
  )
}

function LeetCodeCard() {
  const [data, setData] = useState<LeetCodeStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [view, setView] = useState<LeetView>('topics')
  const [activeTopic, setActiveTopic] = useState(0)

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

  const pieData = data ? buildTopicPie(data.topics ?? []) : []
  const safeActive = pieData.length ? Math.min(activeTopic, pieData.length - 1) : 0
  // Guard against an older agent (pre-topics /leetcode) that omits `recent`.
  const recent = data?.recent ?? []

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
          <div className="flex gap-1">
            <TabButton active={view === 'topics'} onClick={() => setView('topics')}>Topics</TabButton>
            <TabButton active={view === 'difficulty'} onClick={() => setView('difficulty')}>Difficulty</TabButton>
            <TabButton active={view === 'recent'} onClick={() => setView('recent')}>Recent</TabButton>
          </div>

          {view === 'topics' && (
            pieData.length === 0 ? (
              <p className="text-sm py-8 text-center" style={{ color: 'var(--text-muted)' }}>
                No topic data yet — solve a problem and the poller will fill this in.
              </p>
            ) : (
              <>
                <div className="h-44">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={pieData}
                        dataKey="count"
                        nameKey="topic"
                        cx="50%"
                        cy="50%"
                        innerRadius={48}
                        outerRadius={70}
                        paddingAngle={2}
                        activeIndex={safeActive}
                        activeShape={renderActiveTopic}
                        onMouseEnter={(_, i) => setActiveTopic(i)}
                        onClick={(_, i) => setActiveTopic(i)}
                      >
                        {pieData.map((entry, i) => (
                          <Cell key={i} fill={entry.color} stroke="var(--border)" strokeWidth={1} cursor="pointer" />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="flex flex-wrap gap-1 max-h-20 overflow-y-auto">
                  {pieData.map((entry, i) => (
                    <button
                      key={entry.topic}
                      onClick={() => setActiveTopic(i)}
                      className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded-md transition"
                      style={{
                        background: i === safeActive ? 'var(--accent-light)' : 'transparent',
                        color: i === safeActive ? 'var(--accent)' : 'var(--text-muted)',
                      }}
                    >
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: entry.color }} />
                      <span className="truncate max-w-[7rem]">{entry.topic}</span>
                      <span className="opacity-60">{entry.count}</span>
                    </button>
                  ))}
                </div>
              </>
            )
          )}

          {view === 'difficulty' && (
            <div className="h-44">
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
          )}

          {view === 'recent' && (
            <div className="max-h-44 overflow-y-auto flex flex-col gap-1.5 pr-1">
              {recent.length === 0 && (
                <p className="text-sm py-8 text-center" style={{ color: 'var(--text-muted)' }}>
                  No recent solves.
                </p>
              )}
              {recent.map((p) => (
                <div
                  key={p.slug}
                  className="flex flex-col gap-1 pb-1.5"
                  style={{ borderBottom: '1px solid var(--border)' }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium truncate" style={{ color: 'var(--text)' }}>{p.title}</span>
                    <span className="text-[10px] font-semibold shrink-0" style={{ color: difficultyColor(p.difficulty) }}>
                      {p.difficulty}
                    </span>
                  </div>
                  {p.topics.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {p.topics.slice(0, 4).map((t) => (
                        <span
                          key={t}
                          className="text-[10px] px-1 py-0.5 rounded"
                          style={{ background: 'var(--bg)', color: 'var(--text-muted)' }}
                        >
                          {t}
                        </span>
                      ))}
                      {p.topics.length > 4 && (
                        <span className="text-[10px] px-1 py-0.5" style={{ color: 'var(--text-muted)' }}>
                          +{p.topics.length - 4}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

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
