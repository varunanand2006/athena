import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'

// View of the agent's memory vault (Phase 14+). Writing happens through chat
// ("remember that…") or automatically (Phase 15); this view allows reading and
// deletion, making the user the final authority over what's remembered.

interface MemEvent {
  date: string
  kind: string
}

interface NoteMeta {
  slug: string
  title: string
  tags: string[]
  events: MemEvent[]
  created: string
  updated: string
  source: string
  origin?: string
}

interface LinkRef {
  slug: string
  target?: string
  title?: string
  exists?: boolean
}

interface Note extends NoteMeta {
  body: string
  links?: LinkRef[]
  backlinks?: LinkRef[]
}

// Match the agent's Python slugify() so [[wikilink]] targets resolve to the
// same note identity the backend uses.
function slugify(s: string): string {
  const slug = s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return slug || 'untitled'
}

// Rewrite [[Target]] / [[Target|Display]] into markdown links with a note:
// scheme, intercepted by the custom <a> renderer below (Phase 18 graph).
function linkifyWikilinks(body: string): string {
  return body.replace(/\[\[([^\]]+)\]\]/g, (_m, inner: string) => {
    const [target, display] = inner.split('|')
    const slug = slugify(target.trim())
    const text = (display ?? target).trim()
    return `[${text}](note:${slug})`
  })
}

// A small dated chip for a note event (Phase 17 temporal frontmatter).
function EventChip({ ev }: { ev: MemEvent }) {
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium"
      style={{ background: 'rgba(16, 185, 129, 0.12)', color: '#047857' }}
      title={ev.kind || 'event'}
    >
      📅 {formatDate(ev.date)}
      {ev.kind ? <span style={{ opacity: 0.8 }}>· {ev.kind}</span> : null}
    </span>
  )
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

// Chip showing a note's provenance (Phase 21): whether it was captured from a
// conversation, the user's calendar, or a labeled email. Lets the user audit —
// and delete — notes the background source sweeps wrote into the vault.
function OriginChip({ origin }: { origin?: string }) {
  const o = origin || 'conversation'
  const meta: Record<string, { label: string; bg: string; color: string; title: string }> = {
    calendar: {
      label: 'from calendar', bg: 'rgba(139, 92, 246, 0.15)', color: '#6d28d9',
      title: 'Captured automatically from your Google Calendar',
    },
    email: {
      label: 'from email', bg: 'rgba(236, 72, 153, 0.13)', color: '#be185d',
      title: 'Captured automatically from a labeled email',
    },
    conversation: {
      label: 'from conversation', bg: 'rgba(100, 116, 139, 0.12)', color: '#475569',
      title: 'Captured from a chat conversation',
    },
  }
  const m = meta[o] ?? meta.conversation
  return (
    <span
      className="inline-block px-1.5 py-0.5 rounded text-xs font-medium shrink-0"
      style={{ background: m.bg, color: m.color }}
      title={m.title}
    >
      {m.label}
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

  // Upcoming events across all notes (Phase 17): flatten each note's events,
  // keep ones today or later, soonest first. Read-only mirror of the agent's
  // upcoming() tool — same frontmatter, surfaced in the UI.
  const todayStr = new Date().toISOString().slice(0, 10)
  const upcoming = notes
    .flatMap((n) => (n.events ?? []).map((e) => ({ ...e, slug: n.slug, title: n.title })))
    .filter((e) => e.date && e.date >= todayStr)
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(0, 8)

  return (
    <div className="h-full flex flex-col bg-transparent pt-8">
      <div className="flex-1 flex overflow-hidden px-8 pb-8 gap-6 z-10">
        {/* Left column: Upcoming events + note list */}
        <div className="w-80 shrink-0 flex flex-col gap-4 overflow-y-auto">
        {upcoming.length > 0 && (
          <div
            className="glass-panel rounded-3xl shrink-0"
            style={{ border: '1px solid var(--border)', boxShadow: 'var(--shadow-md)' }}
          >
            <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
              <span className="font-semibold text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                Upcoming
              </span>
            </div>
            {upcoming.map((e, i) => (
              <button
                key={`${e.slug}-${e.date}-${e.kind}-${i}`}
                onClick={() => openNote(e.slug)}
                className="w-full text-left px-4 py-2.5 transition-colors"
                style={{ borderBottom: '1px solid var(--border)' }}
              >
                <div className="flex items-center gap-2 mb-0.5">
                  <EventChip ev={e} />
                </div>
                <div className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                  {e.title}
                </div>
              </button>
            ))}
          </div>
        )}

        {/* Note list */}
        <div
          className="flex-1 min-h-0 overflow-y-auto glass-panel rounded-3xl"
          style={{ border: '1px solid var(--border)', boxShadow: 'var(--shadow-md)' }}
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
                  <OriginChip origin={n.origin} />
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
              className="rounded-2xl p-6 max-w-3xl glass-card"
              style={{ boxShadow: 'var(--shadow-md)', border: '1px solid var(--border)' }}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <h2 className="font-semibold text-lg" style={{ color: 'var(--text)' }}>
                    {selected.title}
                  </h2>
                  <SourceBadge source={selected.source} />
                  <OriginChip origin={selected.origin} />
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
                {(selected.events ?? []).map((e, i) => (
                  <EventChip key={`${e.date}-${e.kind}-${i}`} ev={e} />
                ))}
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  created {formatDate(selected.created)} · updated {formatDate(selected.updated)}
                </span>
              </div>
              <div className="prose prose-sm max-w-none" style={{ color: 'var(--text)' }}>
                <ReactMarkdown
                  components={{
                    a: ({ href, children }) => {
                      if (href?.startsWith('note:')) {
                        const slug = href.slice(5)
                        const known = (selected.links ?? []).find((l) => l.slug === slug)
                        const exists = known?.exists !== false
                        return (
                          <a
                            href="#"
                            onClick={(e) => { e.preventDefault(); openNote(slug) }}
                            style={{
                              color: exists ? 'var(--accent)' : 'var(--text-muted)',
                              textDecoration: exists ? 'none' : 'underline dashed',
                              cursor: 'pointer',
                            }}
                            title={exists ? `Open "${slug}"` : `"${slug}" not written yet`}
                          >
                            {children}
                          </a>
                        )
                      }
                      return <a href={href} target="_blank" rel="noreferrer">{children}</a>
                    },
                  }}
                >
                  {linkifyWikilinks(selected.body)}
                </ReactMarkdown>
              </div>

              {(selected.backlinks?.length ?? 0) > 0 && (
                <div className="mt-6 pt-4" style={{ borderTop: '1px solid var(--border)' }}>
                  <p className="text-xs uppercase tracking-wider font-medium mb-2" style={{ color: 'var(--text-muted)' }}>
                    Linked from
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {selected.backlinks!.map((b) => (
                      <button
                        key={b.slug}
                        onClick={() => openNote(b.slug)}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors hover:opacity-80"
                        style={{ background: 'var(--accent-light)', color: 'var(--accent)' }}
                      >
                        ← {b.title ?? b.slug}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
