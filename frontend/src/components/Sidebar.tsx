import { useCallback, useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import axios from 'axios'
import type { Message } from '../App'
import { relativeTime } from '../utils/time'

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
  isCollapsed: boolean
  setIsCollapsed: (collapsed: boolean) => void
}

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2)
}

export default function Sidebar({
  onNewConversation,
  onConversationSelect,
  activeConversationId,
  refreshRef,
  isCollapsed,
  setIsCollapsed
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

  const statusColor = agentOnline === null ? '#FACC15' : agentOnline ? '#4ADE80' : '#F87171'
  const statusLabel = agentOnline === null ? 'Connecting…' : agentOnline ? 'Agent online' : 'Agent offline'

  return (
    <aside
      className={`shrink-0 flex flex-col glass-panel transition-all duration-300 fixed z-50 rounded-2xl overflow-hidden`}
      style={{ 
        width: isCollapsed ? '72px' : '240px',
        top: '1.5rem',
        bottom: '1.5rem',
        left: '1.5rem',
        border: '1px solid var(--border)',
        boxShadow: 'var(--shadow-md)'
      }}
    >
      {/* Logo */}
      <div className={`px-4 pt-5 pb-4 flex flex-col ${isCollapsed ? 'items-center' : ''}`} style={{ borderBottom: '1px solid var(--border)' }}>
        <div className={`flex items-center ${isCollapsed ? 'justify-center' : 'gap-3'} mb-3`}>
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center text-white font-bold text-sm shrink-0 glow-pulse"
            style={{ background: 'var(--accent)', boxShadow: 'var(--glow)' }}
          >
            A
          </div>
          {!isCollapsed && <span className="font-bold text-lg tracking-widest" style={{ color: 'var(--text)', textShadow: 'var(--glow)', textTransform: 'uppercase' }}>Athena</span>}
        </div>
        {!isCollapsed && (
          <div className="flex items-center gap-1.5 ml-1">
            <div
              className="w-1.5 h-1.5 rounded-full shrink-0"
              style={{ background: statusColor, boxShadow: `0 0 6px ${statusColor}80` }}
            />
            <span className="text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
              {statusLabel}
            </span>
          </div>
        )}
      </div>

      {/* Nav links */}
      <nav className="px-3 pt-4 flex flex-col gap-1">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            `flex items-center ${isCollapsed ? 'justify-center px-0' : 'gap-3 px-3'} py-2.5 rounded-xl text-sm font-semibold transition-all ${
              isActive ? 'text-white' : 'text-[var(--text-muted)] hover:text-white hover:bg-[var(--accent-light)]'
            }`
          }
          style={({ isActive }) => (isActive ? { background: 'var(--accent)', boxShadow: 'var(--glow)' } : {})}
          title={isCollapsed ? "Chat" : undefined}
        >
          <IconChat />
          {!isCollapsed && <span>Chat</span>}
        </NavLink>
        <NavLink
          to="/dashboard"
          className={({ isActive }) =>
            `flex items-center ${isCollapsed ? 'justify-center px-0' : 'gap-3 px-3'} py-2.5 rounded-xl text-sm font-semibold transition-all ${
              isActive ? 'text-white' : 'text-[var(--text-muted)] hover:text-white hover:bg-[var(--accent-light)]'
            }`
          }
          style={({ isActive }) => (isActive ? { background: 'var(--accent)', boxShadow: 'var(--glow)' } : {})}
          title={isCollapsed ? "Dashboard" : undefined}
        >
          <IconDashboard />
          {!isCollapsed && <span>Dashboard</span>}
        </NavLink>
        <NavLink
          to="/documents"
          className={({ isActive }) =>
            `flex items-center ${isCollapsed ? 'justify-center px-0' : 'gap-3 px-3'} py-2.5 rounded-xl text-sm font-semibold transition-all ${
              isActive ? 'text-white' : 'text-[var(--text-muted)] hover:text-white hover:bg-[var(--accent-light)]'
            }`
          }
          style={({ isActive }) => (isActive ? { background: 'var(--accent)', boxShadow: 'var(--glow)' } : {})}
          title={isCollapsed ? "Documents" : undefined}
        >
          <IconDocuments />
          {!isCollapsed && <span>Documents</span>}
        </NavLink>
        <NavLink
          to="/memory"
          className={({ isActive }) =>
            `flex items-center ${isCollapsed ? 'justify-center px-0' : 'gap-3 px-3'} py-2.5 rounded-xl text-sm font-semibold transition-all ${
              isActive ? 'text-white' : 'text-[var(--text-muted)] hover:text-white hover:bg-[var(--accent-light)]'
            }`
          }
          style={({ isActive }) => (isActive ? { background: 'var(--accent)', boxShadow: 'var(--glow)' } : {})}
          title={isCollapsed ? "Memory" : undefined}
        >
          <IconMemory />
          {!isCollapsed && <span>Memory</span>}
        </NavLink>
        <NavLink
          to="/system"
          className={({ isActive }) =>
            `flex items-center ${isCollapsed ? 'justify-center px-0' : 'gap-3 px-3'} py-2.5 rounded-xl text-sm font-semibold transition-all ${
              isActive ? 'text-white' : 'text-[var(--text-muted)] hover:text-white hover:bg-[var(--accent-light)]'
            }`
          }
          style={({ isActive }) => (isActive ? { background: 'var(--accent)', boxShadow: 'var(--glow)' } : {})}
          title={isCollapsed ? "System" : undefined}
        >
          <IconSystem />
          {!isCollapsed && <span>System</span>}
        </NavLink>
      </nav>

      {/* Conversation list */}
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto px-3 pt-6 pb-2">
          {conversations.length > 0 && (
            <>
              <p
                className="text-[10px] font-bold px-3 pb-2 uppercase tracking-[0.2em]"
                style={{ color: 'var(--text-muted)' }}
              >
                Recent Comms
              </p>
              {conversations.map((conv) => {
                const isActive = conv.id === activeConversationId
                return (
                  <div
                    key={conv.id}
                    onClick={() => selectConversation(conv)}
                    onMouseEnter={() => setHoveredId(conv.id)}
                    onMouseLeave={() => setHoveredId(null)}
                    className="group flex items-center justify-between gap-2 px-3 py-2 rounded-lg cursor-pointer transition-all mb-1"
                    style={{
                      background: isActive
                        ? 'var(--accent-light)'
                        : hoveredId === conv.id
                        ? 'rgba(30,144,255,0.05)'
                        : 'transparent',
                      borderLeft: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                    }}
                  >
                    <div className="flex-1 min-w-0">
                      <p
                        className="text-sm font-medium truncate"
                        style={{ color: isActive ? 'var(--accent)' : 'var(--text)' }}
                      >
                        {conv.title}
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
      )}
      
      {/* Spacer if collapsed */}
      {isCollapsed && <div className="flex-1" />}

      {/* New conversation & Toggle */}
      <div className={`px-3 pb-4 pt-4 flex ${isCollapsed ? 'flex-col gap-2' : 'gap-2 items-center'}`} style={{ borderTop: '1px solid var(--border)' }}>
        <button
          onClick={onNewConversation}
          className={`flex-1 flex items-center ${isCollapsed ? 'justify-center p-2' : 'gap-2.5 px-3 py-2'} rounded-xl text-sm font-semibold text-[var(--text-muted)] hover:text-white hover:bg-[var(--accent-light)] transition-all`}
          style={{ border: '1px solid var(--border)' }}
          title={isCollapsed ? "New Chat" : undefined}
        >
          <IconPlus />
          {!isCollapsed && <span>New Chat</span>}
        </button>
        <button
          onClick={() => setIsCollapsed(!isCollapsed)}
          className={`shrink-0 flex items-center justify-center p-2 rounded-xl text-[var(--text-muted)] hover:text-white hover:bg-[var(--accent-light)] transition-all`}
          style={{ border: '1px solid var(--border)' }}
          title={isCollapsed ? "Expand" : "Collapse"}
        >
          {isCollapsed ? <IconExpand /> : <IconCollapse />}
        </button>
      </div>
    </aside>
  )
}

function IconChat() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  )
}

function IconDashboard() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
    </svg>
  )
}

function IconDocuments() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="8" y1="13" x2="16" y2="13" />
      <line x1="8" y1="17" x2="16" y2="17" />
    </svg>
  )
}

function IconMemory() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a7 7 0 0 0-7 7c0 2 1 3.5 2 4.5V17a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2v-3.5c1-1 2-2.5 2-4.5a7 7 0 0 0-7-7z" />
      <line x1="9" y1="22" x2="15" y2="22" />
    </svg>
  )
}

function IconSystem() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  )
}

function IconPlus() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  )
}

function IconTrash() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  )
}

function IconExpand() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="13 17 18 12 13 7"></polyline>
      <polyline points="6 17 11 12 6 7"></polyline>
    </svg>
  )
}

function IconCollapse() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="11 17 6 12 11 7"></polyline>
      <polyline points="18 17 13 12 18 7"></polyline>
    </svg>
  )
}
