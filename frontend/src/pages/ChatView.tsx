import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import type { Message } from '../App'

interface Props {
  messages: Message[]
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
  conversationId: string | null
  setConversationId: (id: string) => void
  onConversationUpdate: () => void
}

function LoadingDots() {
  return (
    <div className="flex items-center gap-1.5 px-5 py-4">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="w-2 h-2 rounded-full animate-pulse"
          style={{ background: 'var(--accent)', animationDelay: `${i * 0.2}s`, boxShadow: 'var(--glow)' }}
        />
      ))}
    </div>
  )
}

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2)
}

function ts(date: Date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function ChatView({
  messages,
  setMessages,
  conversationId,
  setConversationId,
  onConversationUpdate,
}: Props) {
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function send() {
    const text = input.trim()
    if (!text || loading) return

    const userMsg: Message = {
      id: uid(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setError(null)
    setLoading(true)

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }

    try {
      const res = await axios.post(
        '/chat',
        { message: text, conversation_id: conversationId },
        { timeout: 120_000 }
      )
      const agentMsg: Message = {
        id: uid(),
        role: 'assistant',
        content: res.data.response,
        timestamp: new Date(),
      }
      setMessages((prev) => [...prev, agentMsg])
      if (!conversationId) {
        setConversationId(res.data.conversation_id)
      }
      onConversationUpdate()
    } catch {
      setError('Agent is unreachable — check that the agent pod is running.')
    } finally {
      setLoading(false)
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  function onInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px'
  }

  return (
    <div className="flex flex-col h-full bg-transparent">
      {/* Header - Floating text */}
      <div className="px-8 py-6 shrink-0 bg-transparent flex flex-col items-start z-10">
        <h1 className="font-bold text-3xl tracking-wide uppercase" style={{ color: 'var(--text)', textShadow: 'var(--glow)' }}>System Chat</h1>
        <p className="text-sm mt-1 uppercase tracking-widest font-semibold" style={{ color: 'var(--text-muted)' }}>Athena Interaction Protocol</p>
      </div>

      {/* Message history */}
      <div className="flex-1 overflow-y-auto px-8 pb-4 space-y-6 scroll-smooth z-10">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center select-none">
            <div
              className="w-16 h-16 rounded-2xl flex items-center justify-center text-white font-bold text-2xl mb-6 glow-pulse"
              style={{ background: 'var(--accent)', boxShadow: 'var(--glow)' }}
            >
              A
            </div>
            <p className="font-bold text-xl uppercase tracking-wider" style={{ color: 'var(--text)', textShadow: 'var(--glow)' }}>
              Awaiting Input
            </p>
            <p className="text-sm mt-2 max-w-md" style={{ color: 'var(--text-muted)' }}>
              Connect to the Athena mainframe. Query documentation, check system status, or run pipeline diagnostics.
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`flex flex-col max-w-[75%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
              <div
                className={`px-5 py-4 text-sm glass-card transition-all ${msg.role === 'user' ? 'text-white rounded-3xl rounded-br-sm' : 'rounded-3xl rounded-bl-sm'}`}
                style={
                  msg.role === 'user'
                    ? { 
                        background: 'rgba(30, 144, 255, 0.15)',
                        backdropFilter: 'blur(12px)',
                        boxShadow: '0 0 20px rgba(30, 144, 255, 0.4), inset 0 0 10px rgba(30, 144, 255, 0.2)', 
                        border: '1px solid rgba(30, 144, 255, 0.5)' 
                      }
                    : {
                        boxShadow: '0 10px 30px rgba(0,0,0,0.5), inset 1px 1px 0 rgba(255,255,255,0.05)',
                        border: '1px solid rgba(255,255,255,0.1)',
                        borderTop: '1px solid rgba(255,255,255,0.2)',
                        borderLeft: '1px solid rgba(255,255,255,0.15)',
                      }
                }
              >
                {msg.role === 'assistant' ? (
                  <div className="prose-athena">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  </div>
                ) : (
                  <span className="font-medium text-[15px]" style={{ whiteSpace: 'pre-wrap', textShadow: '0 0 5px rgba(255,255,255,0.3)' }}>{msg.content}</span>
                )}
              </div>
              <span className="text-xs mt-2 px-2 font-semibold tracking-wider" style={{ color: 'var(--text-muted)' }}>
                {ts(msg.timestamp)}
              </span>
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div
              className="rounded-3xl rounded-bl-sm glass-card"
              style={{
                boxShadow: '0 10px 30px rgba(0,0,0,0.5), inset 1px 1px 0 rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderTop: '1px solid rgba(255,255,255,0.2)',
                borderLeft: '1px solid rgba(255,255,255,0.15)',
              }}
            >
              <LoadingDots />
            </div>
          </div>
        )}

        {error && (
          <div className="flex justify-center">
            <div
              className="px-5 py-3 rounded-2xl text-sm glass-card font-semibold tracking-wide"
              style={{ color: '#ff4d4d', border: '1px solid rgba(255,77,77,0.4)', boxShadow: '0 0 20px rgba(255,77,77,0.2)' }}
            >
              [ERROR] {error}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Floating Input bar */}
      <div className="px-6 pb-6 pt-2 shrink-0 bg-transparent flex justify-center z-20">
        <div
          className="flex items-end gap-3 rounded-[2rem] px-6 py-4 transition-all duration-300 glass-panel max-w-4xl w-full"
          style={{ 
            border: '1px solid rgba(255,255,255,0.1)', 
            borderTop: '1px solid rgba(255,255,255,0.2)',
            borderLeft: '1px solid rgba(255,255,255,0.15)',
            boxShadow: '0 20px 40px rgba(0,0,0,0.8), inset 0 1px 0 rgba(255,255,255,0.1), var(--glow)'
          }}
          onFocus={(e) => {
            ;(e.currentTarget as HTMLDivElement).style.borderColor = 'var(--accent)'
            ;(e.currentTarget as HTMLDivElement).style.boxShadow = '0 20px 50px rgba(30,144,255,0.15), inset 0 1px 0 rgba(255,255,255,0.1), var(--glow)'
          }}
          onBlur={(e) => {
            ;(e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(255,255,255,0.1)'
            ;(e.currentTarget as HTMLDivElement).style.borderTopColor = 'rgba(255,255,255,0.2)'
            ;(e.currentTarget as HTMLDivElement).style.borderLeftColor = 'rgba(255,255,255,0.15)'
            ;(e.currentTarget as HTMLDivElement).style.boxShadow = '0 20px 40px rgba(0,0,0,0.8), inset 0 1px 0 rgba(255,255,255,0.1), var(--glow)'
          }}
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={onInput}
            onKeyDown={onKeyDown}
            placeholder="Message Athena... (Enter to send)"
            rows={1}
            disabled={loading}
            className="flex-1 resize-none bg-transparent text-[15px] outline-none font-medium"
            style={{ color: 'var(--text)', minHeight: '24px', maxHeight: '160px' }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || loading}
            className="shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-white transition-all disabled:opacity-30 hover:scale-105 active:scale-95"
            style={{ background: 'var(--accent)', boxShadow: 'var(--glow)' }}
          >
            <IconSend />
          </button>
        </div>
      </div>
    </div>
  )
}

function IconSend() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  )
}
