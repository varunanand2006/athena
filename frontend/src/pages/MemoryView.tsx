import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'

// View of the agent's memory vault (Phase 14+). Writing happens through chat
// ("remember that…") or automatically (Phase 15); this view allows reading and
// deletion, making the user the final authority over what's remembered.

interface NoteMeta {
  slug: string
  title: string
  tags: string[]
  created: string
  updated: string
  source: string
}

interface Note extends NoteMeta {
  body: string
}

// Badge distinguishing autonomously-captured notes (Phase 15) from ones the
// user explicitly asked to remember. Lets the user audit what the agent decided
// on its own.
function SourceBadge({ source }: { source: string }) {
  const isAuto = source === 'auto'
  return (
    <span
      className="inline-block px-1.5 py-0.5 rounded text-xs font-medium shrink-0"
      style={{
        background: isAuto ? 'rgba(245, 158, 11, 0.15)' : 'rgba(100, 116, 139, 0.12)',
        color: isAuto ? '#b45309' : '#475569',
      }}
      title={isAuto ? 'Captured automatically by Athena' : 'You asked Athena to remember this'}
    >
      {isAuto ? 'auto' : 'you'}
    </span>
  )
}

function formatDate(s: string): string {
  if (!s) return ''
  const d = new Date(s)
  return isNaN(d.getTime()) ? s : d.toLocaleDateString()
}

export default function MemoryView() {
  const [notes, setNotes] = useState<NoteMeta[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [selected, setSelected] = useState<Note | null>(null)
  const [loadingNote, setLoadingNote] = useState(false)

  const fetchNotes = useCallback(async () => {
    try {
      setError(false)
      const res = await axios.get<NoteMeta[]>('/memory', { timeout: 10_000 })
      setNotes(res.data)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchNotes()
  }, [fetchNotes])

  const openNote = useCallback(async (slug: string) => {
    setLoadingNote(true)
    try {
      const res = await axios.get<Note>(`/memory/${slug}`, { timeout: 10_000 })
      setSelected(res.data)
    } catch {
      setSelected(null)
    } finally {
      setLoadingNote(false)
    }
  }, [])

  const deleteNote = useCallback(async (slug: string) => {
    if (!confirm(`Delete memory note "${selected?.title}"?`)) return
    try {
      await axios.delete(`/memory/${slug}`, { timeout: 10_000 })
      setSelected(null)
      await fetchNotes()
    } catch {
      alert('Failed to delete note')
    }
  }, [selected?.title, fetchNotes])

  return (
    <div className="h-full flex flex-col" style={{ background: 'var(--bg)' }}>
      <div className="px-6 py-4 shrink-0" style={{ background: 'var(--bg-panel)', borderBottom: '1px solid var(--border)' }}>
        <h1 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>
          Memory
        </h1>
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
          What Athena remembers — written through chat, read-only here
        </p>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Note list */}
        <div
          className="w-72 shrink-0 overflow-y-auto"
          style={{ borderRight: '1px solid var(--border)', background: 'var(--card)' }}
        >
          <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
            <span className="font-semibold text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
              Notes
            </span>
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {notes.length}
            </span>
          </div>

          {loading && (
            <p className="text-sm py-8 text-center" style={{ color: 'var(--text-muted)' }}>
              Loading…
            </p>
          )}
          {error && <p className="text-sm py-8 text-center" style={{ color: 'var(--accent)' }}>Failed to load memory</p>}
          {!loading && !error && notes.length === 0 && (
            <p className="text-sm py-8 px-4 text-center" style={{ color: 'var(--text-muted)' }}>
              No memories yet. In chat, say "remember that…".
            </p>
          )}

          {notes.map((n) => {
            const isActive = selected?.slug === n.slug
            return (
              <button
                key={n.slug}
                onClick={() => openNote(n.slug)}
                className="w-full text-left px-4 py-3 transition-colors"
                style={{
                  borderBottom: '1px solid var(--border)',
                  background: isActive ? 'var(--accent-light)' : 'transparent',
                  borderLeft: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                }}
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm truncate" style={{ color: 'var(--text)' }}>
                    {n.title}
                  </span>
                  <SourceBadge source={n.source} />
                </div>
                {n.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {n.tags.map((t) => (
                      <span
                        key={t}
                        className="inline-block px-1.5 py-0.5 rounded text-xs"
                        style={{ background: 'var(--accent-light)', color: 'var(--accent)' }}
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
                <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  updated {formatDate(n.updated)}
                </div>
              </button>
            )
          })}
        </div>

        {/* Note detail */}
        <div className="flex-1 overflow-y-auto p-6">
          {loadingNote && (
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
              Loading note…
            </p>
          )}
          {!loadingNote && !selected && (
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
              Select a note to read it.
            </p>
          )}
          {!loadingNote && selected && (
            <div
              className="rounded-2xl p-6 max-w-3xl"
              style={{ background: 'var(--card)', boxShadow: 'var(--shadow-md)', border: '1px solid var(--border)' }}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <h2 className="font-semibold text-lg" style={{ color: 'var(--text)' }}>
                    {selected.title}
                  </h2>
                  <SourceBadge source={selected.source} />
                </div>
                <button
                  onClick={() => deleteNote(selected.slug)}
                  className="px-2 py-1 rounded text-sm transition-colors hover:opacity-70"
                  style={{ color: 'var(--accent)', background: 'rgba(30,144,255,0.1)' }}
                  title="Delete this note"
                >
                  ✕
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-2 mb-4">
                {selected.tags.map((t) => (
                  <span
                    key={t}
                    className="inline-block px-1.5 py-0.5 rounded text-xs"
                    style={{ background: 'var(--accent-light)', color: 'var(--accent)' }}
                  >
                    {t}
                  </span>
                ))}
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  created {formatDate(selected.created)} · updated {formatDate(selected.updated)}
                </span>
              </div>
              <div className="prose prose-sm max-w-none" style={{ color: 'var(--text)' }}>
                <ReactMarkdown>{selected.body}</ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
