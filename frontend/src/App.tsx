import { useState } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import ChatView from './pages/ChatView'
import DashboardView from './pages/DashboardView'

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])

  return (
    <BrowserRouter>
      <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg)' }}>
        <Sidebar onNewConversation={() => setMessages([])} />
        <main className="flex-1 overflow-hidden">
          <Routes>
            <Route path="/" element={<ChatView messages={messages} setMessages={setMessages} />} />
            <Route path="/dashboard" element={<DashboardView messages={messages} />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
