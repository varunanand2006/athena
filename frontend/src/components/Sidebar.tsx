import { useCallback, useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import axios from 'axios'
import type { Message } from '../App'

interface Conversation {
  id: string
  title: string
  updated_at: string
}

interface Props {
  onNewConversation: () => void
  onConversationSelect: (id: string, messages: Message[]) => void
  activeConversationId: string | null
  refreshRef: React.MutableRefObject<(() => void) | null>
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days === 1) return 'yesterday'
  return `${days}d ago`
}

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2)
}

export default function Sidebar({
  onNewConversation,
  onConversationSelect,
  activeConversationId,
  refreshRef,
}: Props) {
  const [agentOnline, setAgentOnline] = useState<boolean | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [hoveredId, setHoveredId] = useState<string | null>(null)

  const fetchConversations = useCallback(async () => {
    try {
      const res = await axios.get<Conversation[]>('/conversations')
      setConversations(res.data)
    } catch {
      // silently ignore — conversation list is non-critical
    }
  }, [])

  useEffect(() => {
    refreshRef.current = fetchConversations
  }, [fetchConversations, refreshRef])

  useEffect(() => {
    async function checkHealth() {
      try {
        await axios.get('/healthz', { timeout: 3000 })
        setAgentOnline(true)
      } catch {
        setAgentOnline(false)
      }
    }
    checkHealth()
    const id = setInterval(checkHealth, 30_000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchConversations()
  }, [fetchConversations])

  async function selectConversation(conv: Conversation) {
    try {
      const res = await axios.get<Array<{ role: string; content: string; created_at: string }>>(
        `/conversations/${conv.id}/messages`
      )
      const loaded: Message[] = res.data.map((m) => ({
        id: uid(),
        role: m.role as 'user' | 'assistant',
        content: m.content,
        timestamp: new Date(m.created_at),
      }))
      onConversationSelect(conv.id, loaded)
    } catch {
      // if load fails just clear the view
      onConversationSelect(conv.id, [])
    }
  }

  async function deleteConversation(e: React.MouseEvent, id: string) {
    e.stopPropagation()
    try {
      await axios.delete(`/conversations/${id}`)
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (activeConversationId === id) {
        onNewConversation()
      }
    } catch {
      // ignore
    }
  }

  const statusColor =
    agentOnline === null ? '#FACC15' : agentOnline ? '#4ADE80' : '#F87171'
  const statusLabel =
    agentOnline === null ? 'Connecting…' : agentOnline ? 'Agent online' : 'Agent offline'

  return (
    <aside
      className="w-56 shrink-0 flex flex-col h-full"
      style={{ background: '#1E1B4B', borderRight: '1px solid rgba(255,255,255,0.07)' }}
    >
      {/* Logo */}
      <div className="px-5 pt-5 pb-4" style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
        <div className="flex items-center gap-2.5 mb-3">
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center text-white font-bold text-sm shrink-0"
            style={{ background: 'var(--accent)' }}
          >
            A
          </div>
          <span className="text-white font-semibold text-base tracking-tight">Athena</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div
            className="w-1.5 h-1.5 rounded-full shrink-0"
            style={{ background: statusColor, boxShadow: `0 0 6px ${statusColor}80` }}
          />
          <span className="text-xs" style={{ color: 'rgba(255,255,255,0.45)' }}>
            {statusLabel}
          </span>
        </div>
      </div>

      {/* Nav links */}
      <nav className="px-3 pt-4 flex flex-col gap-0.5">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
              isActive ? 'text-white' : 'text-white/55 hover:text-white/85 hover:bg-white/5'
            }`
          }
          style={({ isActive }) => (isActive ? { background: 'var(--accent)' } : {})}
        >
          <IconChat />
          Chat
        </NavLink>
        <NavLink
          to="/dashboard"
          className={({ isActive }) =>
            `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
              isActive ? 'text-white' : 'text-white/55 hover:text-white/85 hover:bg-white/5'
            }`
          }
          style={({ isActive }) => (isActive ? { background: 'var(--accent)' } : {})}
        >
          <IconDashboard />
          Dashboard
        </NavLink>
        <NavLink
          to="/documents"
          className={({ isActive }) =>
            `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
              isActive ? 'text-white' : 'text-white/55 hover:text-white/85 hover:bg-white/5'
            }`
          }
          style={({ isActive }) => (isActive ? { background: 'var(--accent)' } : {})}
        >
          <IconDocuments />
          Documents
        </NavLink>
      </nav>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto px-3 pt-3 pb-2">
        {conversations.length > 0 && (
          <>
            <p
              className="text-xs font-medium px-3 pb-1.5 uppercase tracking-wider"
              style={{ color: 'rgba(255,255,255,0.3)' }}
            >
              Recent
            </p>
            {conversations.map((conv) => {
              const isActive = conv.id === activeConversationId
              return (
                <div
                  key={conv.id}
                  onClick={() => selectConversation(conv)}
                  onMouseEnter={() => setHoveredId(conv.id)}
                  onMouseLeave={() => setHoveredId(null)}
                  className="group flex items-center justify-between gap-1 px-3 py-2 rounded-lg cursor-pointer transition-all mb-0.5"
                  style={{
                    background: isActive
                      ? 'rgba(99,102,241,0.25)'
                      : hoveredId === conv.id
                      ? 'rgba(255,255,255,0.05)'
                      : 'transparent',
                    borderLeft: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                  }}
                >
                  <div className="flex-1 min-w-0">
                    <p
                      className="text-xs font-medium truncate"
                      style={{ color: isActive ? '#fff' : 'rgba(255,255,255,0.7)' }}
                    >
                      {conv.title}
                    </p>
                    <p className="text-xs" style={{ color: 'rgba(255,255,255,0.3)' }}>
                      {relativeTime(conv.updated_at)}
                    </p>
                  </div>
                  {hoveredId === conv.id && (
                    <button
                      onClick={(e) => deleteConversation(e, conv.id)}
                      className="shrink-0 p-1 rounded opacity-60 hover:opacity-100 transition-opacity"
                      style={{ color: '#F87171' }}
                      title="Delete conversation"
                    >
                      <IconTrash />
                    </button>
                  )}
                </div>
              )
            })}
          </>
        )}
      </div>

      {/* New conversation */}
      <div className="px-3 pb-4" style={{ borderTop: '1px solid rgba(255,255,255,0.07)', paddingTop: '12px' }}>
        <button
          onClick={onNewConversation}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium text-white/55 hover:text-white/85 hover:bg-white/5 transition-all"
        >
          <IconPlus />
          New conversation
        </button>
      </div>
    </aside>
  )
}

function IconChat() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  )
}

function IconDashboard() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
    </svg>
  )
}

function IconDocuments() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="8" y1="13" x2="16" y2="13" />
      <line x1="8" y1="17" x2="16" y2="17" />
    </svg>
  )
}

function IconPlus() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  )
}

function IconTrash() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  )
}
