import React from 'react'

export default function HomeView() {
  return (
    <div className="h-full overflow-y-auto bg-transparent pt-8">
      <div className="px-8 pb-8 flex flex-col gap-6 z-10 max-w-5xl">
        <h1 className="text-2xl font-bold tracking-wide" style={{ color: 'var(--text)', textShadow: 'var(--glow)' }}>
          Good Morning, Varun
        </h1>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Daily Digest */}
          <div
            className="rounded-2xl p-5 flex flex-col gap-3 glass-card"
            style={{ boxShadow: 'var(--shadow-md)', border: '1px solid var(--border)' }}
          >
            <div className="flex items-center justify-between gap-2">
              <h2 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>
                Daily Digest
              </h2>
              <span className="text-xs font-medium px-2 py-0.5 rounded-full" style={{ background: 'var(--accent-light)', color: 'var(--accent)' }}>
                Today
              </span>
            </div>
            <div className="py-8 flex items-center justify-center">
              <p className="text-sm font-medium animate-pulse" style={{ color: 'var(--text-muted)' }}>
                Loading...
              </p>
            </div>
          </div>

          {/* Quick Actions Placeholder */}
          <div
            className="rounded-2xl p-5 flex flex-col gap-3 glass-card"
            style={{ boxShadow: 'var(--shadow-md)', border: '1px solid var(--border)' }}
          >
            <div className="flex items-center justify-between gap-2">
              <h2 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>
                Quick Actions
              </h2>
            </div>
            <div className="flex flex-col gap-2 mt-2">
              <button
                className="w-full px-4 py-3 text-left rounded-xl text-sm font-medium transition-colors hover:bg-[var(--accent-light)]"
                style={{ border: '1px solid var(--border)', color: 'var(--text)' }}
              >
                Upload a Document
              </button>
              <button
                className="w-full px-4 py-3 text-left rounded-xl text-sm font-medium transition-colors hover:bg-[var(--accent-light)]"
                style={{ border: '1px solid var(--border)', color: 'var(--text)' }}
              >
                Check Latest Internships
              </button>
              <button
                className="w-full px-4 py-3 text-left rounded-xl text-sm font-medium transition-colors hover:bg-[var(--accent-light)]"
                style={{ border: '1px solid var(--border)', color: 'var(--text)' }}
              >
                Review Memory Vault
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
