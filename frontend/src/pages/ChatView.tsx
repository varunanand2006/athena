import { useEffect, useRef, useState, useCallback } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import type { Message, ToolCall } from '../App'

interface Props {
  messages: Message[]
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
  conversationId: string | null
  setConversationId: (id: string) => void
  onConversationUpdate: () => void
}

// ─── Tool display config ───────────────────────────────────────────────

const TOOL_DISPLAY: Record<string, { emoji: string; label: string }> = {
  web_search:           { emoji: '🔍', label: 'Searching the web' },
  find_documents:       { emoji: '📂', label: 'Finding relevant documents' },
  load_document:        { emoji: '📄', label: 'Loading document' },
  lookup_leetcode:      { emoji: '💻', label: 'Looking up LeetCode data' },
  list_documents:       { emoji: '📚', label: 'Listing documents' },
  get_table_of_contents:{ emoji: '📋', label: 'Getting table of contents' },
  get_document_summary: { emoji: '📝', label: 'Getting document summary' },
  write_memory:         { emoji: '🧠', label: 'Writing to memory' },
  list_memories:        { emoji: '🧠', label: 'Listing memories' },
  search_memory:        { emoji: '🧠', label: 'Searching memory vault' },
  upcoming:             { emoji: '📅', label: 'Checking upcoming events' },
  search_email:         { emoji: '📧', label: 'Searching email' },
  get_calendar_events:  { emoji: '📅', label: 'Checking calendar' },
}

function getToolDisplay(name: string) {
  return TOOL_DISPLAY[name] || { emoji: '⚙️', label: name }
}

function getToolDetail(tool: ToolCall): string {
  const inp = tool.input || {}
  if (inp.query) return `"${inp.query}"`
  if (inp.identifier) return `"${inp.identifier}"`
  if (inp.name) return `"${inp.name}"`
  if (inp.title) return `"${inp.title}"`
  if (inp.timeframe) return `${inp.timeframe}`
  return ''
}

// ─── Helpers ────────────────────────────────────────────────────────────

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2)
}

function ts(date: Date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// ─── ThoughtProcess component ───────────────────────────────────────────

function ThoughtProcess({ toolCalls, isStreaming }: { toolCalls: ToolCall[]; isStreaming: boolean }) {
  const [expanded, setExpanded] = useState(true)

  useEffect(() => {
    if (!isStreaming && toolCalls.every((t) => t.status === 'done')) {
      const timer = setTimeout(() => setExpanded(false), 1500)
      return () => clearTimeout(timer)
    }
  }, [isStreaming, toolCalls])

  if (toolCalls.length === 0) return null

  return (
    <div className="thought-process mb-3">
      <button
        onClick={() => setExpanded(!expanded)}
        className="thought-toggle"
      >
        <span className="thought-toggle-icon">{expanded ? '▾' : '▸'}</span>
        <span className="thought-toggle-label">
          {toolCalls.some((t) => t.status === 'running') ? 'Thinking...' : `Used ${toolCalls.length} tool${toolCalls.length > 1 ? 's' : ''}`}
        </span>
      </button>
      {expanded && (
        <div className="thought-items">
          {toolCalls.map((tc, i) => {
            const display = getToolDisplay(tc.tool)
            const detail = getToolDetail(tc)
            return (
              <div key={i} className={`thought-item ${tc.status}`}>
                <span className="thought-icon">{tc.status === 'done' ? '✅' : display.emoji}</span>
                <span className="thought-label">
                  {display.label}{detail ? ` for ${detail}` : ''}
                  {tc.status === 'running' && <span className="thought-dots">...</span>}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── CodeBlock component (copy-to-clipboard) ────────────────────────────

function CodeBlock({ children, className }: { children?: React.ReactNode; className?: string }) {
  const [copied, setCopied] = useState(false)
  const language = className?.replace('language-', '') || ''

  const code = String(children).replace(/\n$/, '')

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // fallback for older browsers
      const ta = document.createElement('textarea')
      ta.value = code
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  return (
    <div className="code-block-wrapper">
      <div className="code-block-header">
        {language && <span className="code-lang-label">{language}</span>}
        <button onClick={handleCopy} className={`copy-btn ${copied ? 'copied' : ''}`}>
          {copied ? (
            <>
              <IconCheck /> Copied!
            </>
          ) : (
            <>
              <IconCopy /> Copy
            </>
          )}
        </button>
      </div>
      <pre>
        <code className={className}>{children}</code>
      </pre>
    </div>
  )
}

// ─── Siri Orb (empty state) ────────────────────────────────────────────

function SiriOrb() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center select-none">
      <div className="flex items-center justify-center mb-6" style={{ transform: 'scale(2.5)' }}>
        <div className="siri-orb-container">
          <div className="siri-blob"></div>
          <div className="siri-blob"></div>
          <div className="siri-blob"></div>
          <div className="siri-blob"></div>
          <div className="siri-blob"></div>
        </div>
      </div>
    </div>
  )
}

// ─── Main ChatView ──────────────────────────────────────────────────────

export default function ChatView({
  messages,
  setMessages,
  conversationId,
  setConversationId,
  onConversationUpdate,
}: Props) {
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  // ─── Shared SSE stream reader ────────────────────────────────────────
  // Drives one /chat/stream request, applying token / tool_start / tool_end /
  // done / error events to the assistant placeholder message. Returns the
  // server's conversation id (from the `done` event) or null. Shared by the
  // input-box send() and the regenerate sendMessage() so the SSE parsing lives
  // in exactly one place.

  const runStream = useCallback(
    async (text: string, assistantId: string, signal: AbortSignal): Promise<string | null> => {
      const res = await fetch('/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
        signal,
      })

      if (!res.ok || !res.body) throw new Error('Stream failed')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullText = ''
      let tools: ToolCall[] = []
      let finalConversationId: string | null = null

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let eventType = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ') && eventType) {
            try {
              const data = JSON.parse(line.slice(6))
              if (eventType === 'token') {
                fullText += data.token
                setMessages((prev) =>
                  prev.map((m) => (m.id === assistantId ? { ...m, content: fullText } : m))
                )
              } else if (eventType === 'tool_start') {
                tools = [...tools, { tool: data.tool, input: data.input || {}, status: 'running' }]
                setMessages((prev) =>
                  prev.map((m) => (m.id === assistantId ? { ...m, toolCalls: [...tools] } : m))
                )
              } else if (eventType === 'tool_end') {
                tools = tools.map((t) =>
                  t.tool === data.tool && t.status === 'running'
                    ? { ...t, status: 'done' as const, output: data.output }
                    : t
                )
                setMessages((prev) =>
                  prev.map((m) => (m.id === assistantId ? { ...m, toolCalls: [...tools] } : m))
                )
              } else if (eventType === 'done') {
                finalConversationId = data.conversation_id
                // Final content from server is authoritative
                if (data.response) {
                  fullText = data.response
                  setMessages((prev) =>
                    prev.map((m) => (m.id === assistantId ? { ...m, content: fullText } : m))
                  )
                }
              } else if (eventType === 'error') {
                setError(data.error || 'Unknown streaming error')
              }
            } catch {
              // malformed JSON line, skip
            }
            eventType = ''
          }
        }
      }

      return finalConversationId
    },
    [conversationId, setMessages]
  )

  // ─── Streaming send ──────────────────────────────────────────────────

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming) return

    const userMsg: Message = {
      id: uid(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    }

    const assistantId = uid()
    const placeholderMsg: Message = {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      toolCalls: [],
    }

    setMessages((prev) => [...prev, userMsg, placeholderMsg])
    setInput('')
    setError(null)
    setStreaming(true)

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const finalConversationId = await runStream(text, assistantId, controller.signal)

      if (finalConversationId && !conversationId) {
        setConversationId(finalConversationId)
      }
      onConversationUpdate()
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // User stopped generation — keep partial text
        onConversationUpdate()
      } else {
        // Fallback to non-streaming endpoint
        try {
          const res = await axios.post(
            '/chat',
            { message: text, conversation_id: conversationId },
            { timeout: 120_000 }
          )
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, content: res.data.response, toolCalls: [] }
                : m
            )
          )
          if (!conversationId) {
            setConversationId(res.data.conversation_id)
          }
          onConversationUpdate()
        } catch {
          setError('Agent is unreachable — check that the agent pod is running.')
          // Remove the empty placeholder
          setMessages((prev) => prev.filter((m) => m.id !== assistantId))
        }
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [input, streaming, conversationId, runStream, setMessages, setConversationId, onConversationUpdate])

  function stopGeneration() {
    abortRef.current?.abort()
  }

  function regenerate() {
    // Find the last user message and re-send it
    const lastUserIdx = [...messages].reverse().findIndex((m) => m.role === 'user')
    if (lastUserIdx === -1) return
    const idx = messages.length - 1 - lastUserIdx
    const lastUserMsg = messages[idx]

    // Remove the last assistant message(s) after the last user message
    setMessages((prev) => prev.slice(0, idx + 1))
    setInput(lastUserMsg.content)
    // We set input and will trigger send on next tick
    setTimeout(() => {
      // Directly trigger send with the message content
      sendMessage(lastUserMsg.content)
    }, 50)
  }

  const sendMessage = useCallback(async (text: string) => {
    if (!text || streaming) return

    const assistantId = uid()
    const placeholderMsg: Message = {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      toolCalls: [],
    }

    setMessages((prev) => [...prev, placeholderMsg])
    setInput('')
    setError(null)
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const finalConversationId = await runStream(text, assistantId, controller.signal)

      if (finalConversationId && !conversationId) {
        setConversationId(finalConversationId)
      }
      onConversationUpdate()
    } catch (err: unknown) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        setError('Streaming failed — check that the agent pod is running.')
        setMessages((prev) => prev.filter((m) => m.id !== assistantId))
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }, [streaming, conversationId, runStream, setMessages, setConversationId, onConversationUpdate])

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

  // Check if the last message is an assistant message (for regenerate button)
  const lastMsg = messages[messages.length - 1]
  const showRegenerate = !streaming && lastMsg?.role === 'assistant' && lastMsg.content.length > 0

  // Custom markdown components for code blocks
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markdownComponents: Record<string, React.ComponentType<any>> = {
    code({ className, children }: { className?: string; children?: React.ReactNode }) {
      const isBlock = className?.startsWith('language-') || (typeof children === 'string' && children.includes('\n'))
      if (isBlock) {
        return <CodeBlock className={className}>{children}</CodeBlock>
      }
      return <code className={className}>{children}</code>
    },
  }

  return (
    <div className="flex flex-col h-full bg-transparent pt-6">

      {/* Message history */}
      <div className="flex-1 overflow-y-auto px-8 pb-4 space-y-6 scroll-smooth z-10">
        {messages.length === 0 && <SiriOrb />}

        {messages.map((msg, idx) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`flex flex-col max-w-[75%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
              {/* Thought Process (tool calls) */}
              {msg.role === 'assistant' && msg.toolCalls && msg.toolCalls.length > 0 && (
                <ThoughtProcess
                  toolCalls={msg.toolCalls}
                  isStreaming={streaming && idx === messages.length - 1}
                />
              )}

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
                        boxShadow: '0 5px 15px rgba(0,0,0,0.5), inset 1px 1px 0 rgba(255,255,255,0.05)',
                        border: '1px solid rgba(255,255,255,0.1)',
                        borderTop: '1px solid rgba(255,255,255,0.2)',
                        borderLeft: '1px solid rgba(255,255,255,0.15)',
                      }
                }
              >
                {msg.role === 'assistant' ? (
                  <div className="prose-athena">
                    {msg.content ? (
                      <ReactMarkdown components={markdownComponents}>{msg.content}</ReactMarkdown>
                    ) : streaming && idx === messages.length - 1 ? (
                      <span className="streaming-cursor" />
                    ) : null}
                    {streaming && idx === messages.length - 1 && msg.content && (
                      <span className="streaming-cursor" />
                    )}
                  </div>
                ) : (
                  <span className="font-medium text-[15px]" style={{ whiteSpace: 'pre-wrap', textShadow: '0 0 5px rgba(255,255,255,0.3)' }}>{msg.content}</span>
                )}
              </div>

              <span className="text-xs mt-2 px-2 font-semibold tracking-wider" style={{ color: 'var(--text-muted)' }}>
                {ts(msg.timestamp)}
              </span>

              {/* Regenerate button on last assistant message */}
              {showRegenerate && idx === messages.length - 1 && msg.role === 'assistant' && (
                <button onClick={regenerate} className="regenerate-btn mt-2">
                  <IconRegenerate /> Regenerate
                </button>
              )}
            </div>
          </div>
        ))}

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
          className="flex items-center gap-3 rounded-[2rem] px-6 py-4 transition-all duration-300 glass-panel max-w-4xl w-full"
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
            disabled={streaming}
            className="flex-1 resize-none bg-transparent text-[15px] outline-none font-medium"
            style={{ color: 'var(--text)', minHeight: '24px', maxHeight: '160px' }}
          />
          {streaming ? (
            <button
              onClick={stopGeneration}
              className="shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-white transition-all hover:scale-105 active:scale-95 stop-btn"
              title="Stop generation"
            >
              <IconStop />
            </button>
          ) : (
            <button
              onClick={send}
              disabled={!input.trim()}
              className="shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-white transition-all disabled:opacity-30 hover:scale-105 active:scale-95"
              style={{ background: 'var(--accent)', boxShadow: 'var(--glow)' }}
            >
              <IconSend />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Icons ──────────────────────────────────────────────────────────────

function IconSend() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  )
}

function IconStop() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
      <rect x="4" y="4" width="16" height="16" rx="2" />
    </svg>
  )
}

function IconRegenerate() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  )
}

function IconCopy() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  )
}

function IconCheck() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  )
}
