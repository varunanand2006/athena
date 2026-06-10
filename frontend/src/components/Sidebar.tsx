import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import axios from 'axios'

interface Props {
  onNewConversation: () => void
}

export default function Sidebar({ onNewConversation }: Props) {
  const [agentOnline, setAgentOnline] = useState<boolean | null>(null)

  useEffect(() => {
    async function check() {
      try {
        await axios.get('/healthz', { timeout: 3000 })
        setAgentOnline(true)
      } catch {
        setAgentOnline(false)
      }
    }
    check()
    const id = setInterval(check, 30_000)
    return () => clearInterval(id)
  }, [])

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
      <nav className="flex-1 px-3 py-4 flex flex-col gap-0.5">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
              isActive ? 'text-white' : 'text-white/55 hover:text-white/85 hover:bg-white/5'
            }`
          }
          style={({ isActive }) =>
            isActive ? { background: 'var(--accent)' } : {}
          }
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
          style={({ isActive }) =>
            isActive ? { background: 'var(--accent)' } : {}
          }
        >
          <IconDashboard />
          Dashboard
        </NavLink>
      </nav>

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

function IconPlus() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  )
}
