import { useState } from 'react'
import DocumentsView from './DocumentsView'
import MemoryView from './MemoryView'

export default function LibraryView() {
  const [activeTab, setActiveTab] = useState<'documents' | 'memory'>('documents')

  return (
    <div className="h-full flex flex-col bg-transparent">
      {/* Tabs */}
      <div className="pt-8 px-8 flex gap-6 shrink-0 z-20">
        <button
          onClick={() => setActiveTab('documents')}
          className={`text-lg font-bold transition-all ${
            activeTab === 'documents'
              ? 'text-[var(--text)]'
              : 'text-[var(--text-muted)] hover:text-[var(--text)]'
          }`}
          style={{ textShadow: activeTab === 'documents' ? 'var(--glow)' : 'none' }}
        >
          Documents
        </button>
        <button
          onClick={() => setActiveTab('memory')}
          className={`text-lg font-bold transition-all ${
            activeTab === 'memory'
              ? 'text-[var(--text)]'
              : 'text-[var(--text-muted)] hover:text-[var(--text)]'
          }`}
          style={{ textShadow: activeTab === 'memory' ? 'var(--glow)' : 'none' }}
        >
          Memory Vault
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden relative">
        {activeTab === 'documents' ? <DocumentsView /> : <MemoryView />}
      </div>
    </div>
  )
}
