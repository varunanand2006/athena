import { useCallback, useEffect, useRef, useState } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import ChatView from './pages/ChatView'
import DashboardView from './pages/DashboardView'
import DocumentsView from './pages/DocumentsView'
import MemoryView from './pages/MemoryView'
import SystemView from './pages/SystemView'

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const refreshSidebarRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      document.documentElement.style.setProperty('--mouse-x', `${e.clientX}px`)
      document.documentElement.style.setProperty('--mouse-y', `${e.clientY}px`)
    }
    window.addEventListener('mousemove', handleMouseMove)
    return () => window.removeEventListener('mousemove', handleMouseMove)
  }, [])

  const handleNewConversation = useCallback(() => {
    setMessages([])
    setConversationId(null)
  }, [])

  const handleConversationSelect = useCallback(
    (id: string, loadedMessages: Message[]) => {
      setConversationId(id)
      setMessages(loadedMessages)
    },
    []
  )

  const handleConversationUpdate = useCallback(() => {
    refreshSidebarRef.current?.()
  }, [])

  return (
    <BrowserRouter>
      {/* Root container is now transparent to let body background show */}
      <div className="flex h-screen overflow-hidden transparent">
        <Sidebar
          onNewConversation={handleNewConversation}
          onConversationSelect={handleConversationSelect}
          activeConversationId={conversationId}
          refreshRef={refreshSidebarRef}
          isCollapsed={sidebarCollapsed}
          setIsCollapsed={setSidebarCollapsed}
        />
        <main 
          className="flex-1 overflow-hidden transition-all duration-300 relative h-full flex flex-col"
          style={{ marginLeft: sidebarCollapsed ? '100px' : '268px' }}
        >
          <Routes>
            <Route
              path="/"
              element={
                <ChatView
                  messages={messages}
                  setMessages={setMessages}
                  conversationId={conversationId}
                  setConversationId={setConversationId}
                  onConversationUpdate={handleConversationUpdate}
                />
              }
            />
            <Route path="/dashboard" element={<DashboardView messages={messages} />} />
            <Route path="/documents" element={<DocumentsView />} />
            <Route path="/memory" element={<MemoryView />} />
            <Route path="/system" element={<SystemView />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
