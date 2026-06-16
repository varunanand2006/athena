

export default function HomeView() {
  return (
    <div className="h-full overflow-y-auto bg-transparent pt-8">
      <div className="px-8 pb-8 flex flex-col gap-6 z-10 max-w-5xl">
        <h1 className="text-2xl font-bold tracking-wide" style={{ color: 'var(--text)', textShadow: 'var(--glow)' }}>
          Good Morning, Varun
        </h1>

        <div className="flex justify-center mt-12">
          {/* Daily Digest */}
          <div
            className="rounded-2xl p-8 flex flex-col gap-4 glass-card w-full max-w-3xl"
            style={{ boxShadow: 'var(--shadow-md)', border: '1px solid var(--border)' }}
          >
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-semibold text-lg" style={{ color: 'var(--text)' }}>
                Daily Digest
              </h2>
              <span className="text-sm font-medium px-3 py-1 rounded-full" style={{ background: 'var(--accent-light)', color: 'var(--accent)' }}>
                Today
              </span>
            </div>
            <div className="py-24 flex items-center justify-center">
              <p className="text-lg font-medium animate-pulse" style={{ color: 'var(--text-muted)' }}>
                Loading...
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
