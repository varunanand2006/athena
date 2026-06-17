import { useState } from 'react'
import DocumentsView from './DocumentsView'
import MemoryView from './MemoryView'
import GraphView from './GraphView'

type Tab = 'documents' | 'memory' | 'graph'

const TABS: { id: Tab; label: string }[] = [
  { id: 'documents', label: 'Documents' },
  { id: 'memory', label: 'Memory Vault' },
  { id: 'graph', label: 'Graph' },
]

export default function LibraryView() {
  const [activeTab, setActiveTab] = useState<Tab>('documents')

  return (
    <div className="h-full flex flex-col bg-transparent">
      {/* Tabs */}
      <div className="pt-8 px-8 flex gap-6 shrink-0 z-20">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`text-lg font-bold transition-all ${
              activeTab === t.id
                ? 'text-[var(--text)]'
                : 'text-[var(--text-muted)] hover:text-[var(--text)]'
            }`}
            style={{ textShadow: activeTab === t.id ? 'var(--glow)' : 'none' }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden relative">
        {activeTab === 'documents' && <DocumentsView />}
        {activeTab === 'memory' && <MemoryView />}
        {activeTab === 'graph' && <GraphView />}
      </div>
    </div>
  )
}
