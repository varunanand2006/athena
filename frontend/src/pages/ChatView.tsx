import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import type { Message } from '../App'

interface Props {
  messages: Message[]
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
}

function LoadingDots() {
  return (
    <div className="flex items-center gap-1 px-4 py-3">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="w-1.5 h-1.5 rounded-full animate-bounce"
          style={{ background: 'var(--text-muted)', animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  )
}

function ts(date: Date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function ChatView({ messages, setMessages }: Props) {
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
      id: crypto.randomUUID(),
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
      const res = await axios.post('/chat', { message: text }, { timeout: 120_000 })
      const agentMsg: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: res.data.response,
        timestamp: new Date(),
      }
      setMessages((prev) => [...prev, agentMsg])
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
    <div className="flex flex-col h-full" style={{ background: 'var(--bg)' }}>
      {/* Header */}
      <div className="px-6 py-4 bg-white shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <h1 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>Chat</h1>
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>Ask Athena anything</p>
      </div>

      {/* Message history */}
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center select-none">
            <div
              className="w-12 h-12 rounded-2xl flex items-center justify-center text-white font-bold text-xl mb-4"
              style={{ background: 'var(--accent)' }}
            >
              A
            </div>
            <p className="font-semibold text-base" style={{ color: 'var(--text)' }}>
              How can I help?
            </p>
            <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
              Ask about internships, LeetCode, your documents, or anything else.
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`flex flex-col max-w-[72%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
              <div
                className={`px-4 py-3 rounded-2xl text-sm ${msg.role === 'user' ? 'text-white rounded-br-sm' : 'rounded-bl-sm'}`}
                style={
                  msg.role === 'user'
                    ? { background: 'var(--accent)' }
                    : {
                        background: 'var(--card)',
                        boxShadow: 'var(--shadow)',
                        border: '1px solid var(--border)',
                      }
                }
              >
                {msg.role === 'assistant' ? (
                  <div className="prose-athena">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  </div>
                ) : (
                  <span style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</span>
                )}
              </div>
              <span className="text-xs mt-1 px-1" style={{ color: 'var(--text-muted)' }}>
                {ts(msg.timestamp)}
              </span>
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div
              className="rounded-2xl rounded-bl-sm"
              style={{
                background: 'var(--card)',
                boxShadow: 'var(--shadow)',
                border: '1px solid var(--border)',
              }}
            >
              <LoadingDots />
            </div>
          </div>
        )}

        {error && (
          <div className="flex justify-center">
            <div
              className="px-4 py-2.5 rounded-xl text-sm text-red-700"
              style={{ background: '#FEF2F2', border: '1px solid #FECACA' }}
            >
              {error}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="px-6 py-4 bg-white shrink-0" style={{ borderTop: '1px solid var(--border)' }}>
        <div
          className="flex items-end gap-3 rounded-2xl px-4 py-3 transition-colors"
          style={{ background: 'var(--bg)', border: '1.5px solid var(--border)' }}
          onFocus={(e) => {
            ;(e.currentTarget as HTMLDivElement).style.borderColor = 'var(--accent)'
          }}
          onBlur={(e) => {
            ;(e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border)'
          }}
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={onInput}
            onKeyDown={onKeyDown}
            placeholder="Message Athena… (Enter to send, Shift+Enter for newline)"
            rows={1}
            disabled={loading}
            className="flex-1 resize-none bg-transparent text-sm outline-none"
            style={{ color: 'var(--text)', minHeight: '24px', maxHeight: '160px' }}
          />
          <button
            onClick={send}
            disabled={!input.trim() || loading}
            className="shrink-0 w-8 h-8 rounded-xl flex items-center justify-center text-white transition-opacity disabled:opacity-40"
            style={{ background: 'var(--accent)' }}
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
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  )
}
