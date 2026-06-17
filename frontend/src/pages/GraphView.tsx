import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'

// Dependency-free force-directed view of the memory wiki graph (Phase 18).
// Nodes are notes, edges are [[wikilinks]]. A small cooling simulation
// (repulsion + edge springs + centering gravity) runs in requestAnimationFrame
// and pauses once settled; interactions reheat it. No graph library — consistent
// with the project's minimal-dependency ethos.

interface GNode {
  slug: string
  title: string
  source: string
  tags: string[]
  degree: number
  x: number
  y: number
  vx: number
  vy: number
  fx: number | null
  fy: number | null
}

interface GEdge { source: string; target: string }

const AUTO = '#ffb000'   // synthesized (auto) notes — neon amber
const YOU = '#00f3ff'    // explicit (you) notes — neon cyan

function radius(degree: number): number {
  return 5 + Math.sqrt(degree) * 2.6
}

export default function GraphView() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [, setTick] = useState(0)
  const [hover, setHover] = useState<string | null>(null)
  const [hoverDetail, setHoverDetail] = useState<{slug: string, summary: string} | null>(null)

  useEffect(() => {
    if (!hover) {
      setHoverDetail(null)
      return
    }
    const targetSlug = hover
    const timer = setTimeout(async () => {
      try {
        const res = await axios.get<{body: string}>(`/memory/${targetSlug}`)
        const text = res.data.body.replace(/[#*`_[\]]/g, '').substring(0, 150).trim()
        setHoverDetail({ slug: targetSlug, summary: text + (res.data.body.length > 150 ? '...' : '') })
      } catch {
        // ignore
      }
    }, 250)
    return () => clearTimeout(timer)
  }, [hover])

  const containerRef = useRef<HTMLDivElement>(null)
  const nodesRef = useRef<GNode[]>([])
  const edgesRef = useRef<GEdge[]>([])
  const adjRef = useRef<Map<string, Set<string>>>(new Map())
  const sizeRef = useRef({ w: 900, h: 650 })
  const alphaRef = useRef(1)
  const rafRef = useRef<number | null>(null)

  const viewRef = useRef({ scale: 1, tx: 0, ty: 0 })
  const [view, setViewState] = useState({ scale: 1, tx: 0, ty: 0 })
  const setView = useCallback((v: { scale: number; tx: number; ty: number }) => {
    viewRef.current = v
    setViewState(v)
  }, [])

  const dragRef = useRef<string | null>(null)
  const panRef = useRef<{ px: number; py: number; tx: number; ty: number } | null>(null)

  const reheat = useCallback(() => {
    alphaRef.current = Math.max(alphaRef.current, 0.5)
  }, [])

  // --- load graph ----------------------------------------------------------
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await axios.get<{ nodes: Omit<GNode, 'x' | 'y' | 'vx' | 'vy' | 'fx' | 'fy'>[]; edges: GEdge[] }>(
          '/memory/graph',
          { timeout: 10_000 },
        )
        if (cancelled) return
        const w = containerRef.current?.clientWidth ?? 900
        const h = containerRef.current?.clientHeight ?? 650
        sizeRef.current = { w, h }
        const N = res.data.nodes.length || 1
        const ring = Math.min(w, h) * 0.32
        nodesRef.current = res.data.nodes.map((n, i) => {
          const ang = (i / N) * Math.PI * 2
          return { ...n, x: w / 2 + Math.cos(ang) * ring, y: h / 2 + Math.sin(ang) * ring, vx: 0, vy: 0, fx: null, fy: null }
        })
        edgesRef.current = res.data.edges
        const adj = new Map<string, Set<string>>()
        for (const n of res.data.nodes) adj.set(n.slug, new Set())
        for (const e of res.data.edges) {
          adj.get(e.source)?.add(e.target)
          adj.get(e.target)?.add(e.source)
        }
        adjRef.current = adj
        alphaRef.current = 1
        setView({ scale: 1, tx: 0, ty: 0 })
      } catch {
        if (!cancelled) setError(true)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [setView])

  // --- simulation loop -----------------------------------------------------
  useEffect(() => {
    const step = () => {
      const nodes = nodesRef.current
      const edges = edgesRef.current
      const { w, h } = sizeRef.current
      const alpha = alphaRef.current
      const active = nodes.length > 0 && (alpha > 0.004 || dragRef.current !== null)

      if (active) {
        const idx = new Map(nodes.map((n, i) => [n.slug, i]))
        // repulsion (Coulomb)
        for (let i = 0; i < nodes.length; i++) {
          for (let j = i + 1; j < nodes.length; j++) {
            const a = nodes[i], b = nodes[j]
            let dx = a.x - b.x, dy = a.y - b.y
            let d2 = dx * dx + dy * dy
            if (d2 < 0.01) { d2 = 0.01; dx = Math.random() - 0.5; dy = Math.random() - 0.5 }
            const d = Math.sqrt(d2)
            const f = (2600 / d2) * alpha
            const fx = (dx / d) * f, fy = (dy / d) * f
            a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
          }
        }
        // edge springs (Hooke toward target length)
        const target = 95
        for (const e of edges) {
          const ai = idx.get(e.source), bi = idx.get(e.target)
          if (ai === undefined || bi === undefined) continue
          const a = nodes[ai], b = nodes[bi]
          const dx = b.x - a.x, dy = b.y - a.y
          const d = Math.sqrt(dx * dx + dy * dy) || 0.01
          const f = (d - target) * 0.02 * alpha
          const fx = (dx / d) * f, fy = (dy / d) * f
          a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
        }
        // centering gravity + integrate
        const g = 0.018
        for (const n of nodes) {
          if (n.fx !== null && n.fy !== null) { n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; continue }
          n.vx += (w / 2 - n.x) * g * alpha
          n.vy += (h / 2 - n.y) * g * alpha
          n.vx *= 0.86; n.vy *= 0.86
          n.x += n.vx; n.y += n.vy
        }
        alphaRef.current = Math.max(0, alpha * 0.99)
        setTick((t) => (t + 1) % 1_000_000)
      }
      rafRef.current = requestAnimationFrame(step)
    }
    rafRef.current = requestAnimationFrame(step)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [])

  // --- pointer coords in graph space --------------------------------------
  const toGraph = useCallback((clientX: number, clientY: number) => {
    const rect = containerRef.current?.getBoundingClientRect()
    const { scale, tx, ty } = viewRef.current
    const sx = clientX - (rect?.left ?? 0)
    const sy = clientY - (rect?.top ?? 0)
    return { x: (sx - tx) / scale, y: (sy - ty) / scale }
  }, [])

  // --- global drag / pan ---------------------------------------------------
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (dragRef.current) {
        const p = toGraph(e.clientX, e.clientY)
        const n = nodesRef.current.find((nd) => nd.slug === dragRef.current)
        if (n) { n.fx = p.x; n.fy = p.y }
        reheat()
      } else if (panRef.current) {
        const dx = e.clientX - panRef.current.px
        const dy = e.clientY - panRef.current.py
        setView({ scale: viewRef.current.scale, tx: panRef.current.tx + dx, ty: panRef.current.ty + dy })
      }
    }
    const onUp = () => {
      if (dragRef.current) {
        const n = nodesRef.current.find((nd) => nd.slug === dragRef.current)
        if (n) { n.fx = null; n.fy = null }
        dragRef.current = null
      }
      panRef.current = null
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [reheat, setView, toGraph])

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const { scale, tx, ty } = viewRef.current
    const rect = containerRef.current?.getBoundingClientRect()
    const sx = e.clientX - (rect?.left ?? 0)
    const sy = e.clientY - (rect?.top ?? 0)
    const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1
    const next = Math.min(3, Math.max(0.3, scale * factor))
    // zoom toward the cursor
    setView({ scale: next, tx: sx - (sx - tx) * (next / scale), ty: sy - (sy - ty) * (next / scale) })
  }, [setView])

  const onBackgroundDown = useCallback((e: React.MouseEvent) => {
    panRef.current = { px: e.clientX, py: e.clientY, tx: viewRef.current.tx, ty: viewRef.current.ty }
  }, [])

  const onNodeDown = useCallback((e: React.MouseEvent, slug: string) => {
    e.stopPropagation()
    dragRef.current = slug
    reheat()
  }, [reheat])

  const resetView = useCallback(() => { setView({ scale: 1, tx: 0, ty: 0 }); reheat() }, [reheat, setView])

  const nodes = nodesRef.current
  const edges = edgesRef.current
  const neighbors = useMemo(
    () => (hover ? adjRef.current.get(hover) ?? new Set<string>() : null),
    [hover],
  )
  const hoverNode = hover ? nodes.find((n) => n.slug === hover) : null

  const isLit = (slug: string) => !hover || slug === hover || (neighbors?.has(slug) ?? false)

  return (
    <div className="h-full flex flex-col bg-transparent pt-8">
      <div className="px-8 pb-8 flex-1 min-h-0 z-10">
        <div
          ref={containerRef}
          className="relative h-full w-full rounded-3xl overflow-hidden glass-panel"
          style={{ border: '1px solid rgba(0, 243, 255, 0.2)', boxShadow: '0 0 30px rgba(0, 243, 255, 0.05) inset', cursor: 'grab', background: 'rgba(2, 6, 15, 0.7)' }}
        >
          {loading && (
            <p className="absolute inset-0 flex items-center justify-center text-sm" style={{ color: 'var(--text-muted)' }}>
              Building graph…
            </p>
          )}
          {error && (
            <p className="absolute inset-0 flex items-center justify-center text-sm" style={{ color: 'var(--accent)' }}>
              Failed to load memory graph
            </p>
          )}

          {!loading && !error && nodes.length === 0 && (
            <p className="absolute inset-0 flex items-center justify-center text-sm" style={{ color: 'var(--text-muted)' }}>
              No memories to graph yet.
            </p>
          )}

          {!loading && !error && nodes.length > 0 && (
            <svg
              width="100%"
              height="100%"
              onMouseDown={onBackgroundDown}
              onWheel={onWheel}
              style={{ display: 'block' }}
            >
              <defs>
                <filter id="jarvis-glow" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur stdDeviation="4" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
                <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
                  <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(0, 243, 255, 0.04)" strokeWidth="1"/>
                </pattern>
              </defs>
              <rect width="100%" height="100%" fill="url(#grid)" />
              <g transform={`translate(${view.tx},${view.ty}) scale(${view.scale})`}>
                {edges.map((e, i) => {
                  const a = nodes.find((n) => n.slug === e.source)
                  const b = nodes.find((n) => n.slug === e.target)
                  if (!a || !b) return null
                  const lit = !hover || (isLit(e.source) && isLit(e.target) && (e.source === hover || e.target === hover))
                  return (
                    <line
                      key={i}
                      x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                      stroke={lit ? '#00f3ff' : 'rgba(0, 243, 255, 0.15)'}
                      strokeWidth={lit ? 1.5 : 0.5}
                      strokeOpacity={lit ? 0.6 : 0.3}
                      style={{ filter: lit ? 'url(#jarvis-glow)' : 'none', transition: 'stroke-opacity 0.3s' }}
                    />
                  )
                })}
                {nodes.map((n) => {
                  const lit = isLit(n.slug)
                  const r = radius(n.degree)
                  const color = n.source === 'auto' ? AUTO : YOU
                  const showLabel = n.degree >= 5 || n.slug === hover || (neighbors?.has(n.slug) ?? false) || view.scale > 1.4
                  return (
                    <g
                      key={n.slug}
                      transform={`translate(${n.x},${n.y})`}
                      onMouseDown={(e) => onNodeDown(e, n.slug)}
                      onMouseEnter={() => setHover(n.slug)}
                      onMouseLeave={() => setHover((h) => (h === n.slug ? null : h))}
                      style={{ cursor: 'pointer' }}
                    >
                      <circle
                        r={r}
                        fill={color}
                        fillOpacity={lit ? 0.9 : 0.15}
                        stroke={n.slug === hover ? '#fff' : color}
                        strokeWidth={n.slug === hover ? 2 : 1}
                        style={{ filter: lit ? 'url(#jarvis-glow)' : 'none', transition: 'fill-opacity 0.3s' }}
                      />
                      {showLabel && (
                        <text
                          x={r + 6}
                          y={4}
                          fontSize={11}
                          fontFamily="monospace"
                          fill={lit ? '#fff' : 'rgba(0, 243, 255, 0.5)'}
                          fillOpacity={lit ? 1 : 0.5}
                          style={{ pointerEvents: 'none', userSelect: 'none', filter: lit ? 'url(#jarvis-glow)' : 'none' }}
                        >
                          {n.title.toUpperCase()}
                        </text>
                      )}
                    </g>
                  )
                })}
              </g>
            </svg>
          )}

          {/* Legend + controls overlay */}
          {!loading && !error && nodes.length > 0 && (
            <div className="absolute top-4 left-4 flex flex-col gap-2 text-xs font-mono">
              <div className="flex items-center gap-4 px-4 py-2 rounded-lg" style={{ background: 'rgba(2, 6, 23, 0.7)', border: '1px solid rgba(0, 243, 255, 0.3)', color: '#00f3ff', backdropFilter: 'blur(4px)', boxShadow: '0 0 10px rgba(0, 243, 255, 0.1)' }}>
                <span className="flex items-center gap-2 tracking-widest"><Dot color={YOU} /> USER</span>
                <span className="flex items-center gap-2 tracking-widest"><Dot color={AUTO} /> SYS</span>
                <span className="opacity-60 ml-2 tracking-widest">[{nodes.length} N // {edges.length} E]</span>
              </div>
            </div>
          )}

          <div className="absolute top-4 right-4 flex gap-2 font-mono">
            <button
              onClick={resetView}
              className="px-3 py-1.5 rounded-lg text-xs font-bold transition-all hover:bg-[rgba(0,243,255,0.1)]"
              style={{ border: '1px solid rgba(0, 243, 255, 0.3)', color: '#00f3ff', background: 'rgba(2, 6, 23, 0.7)', backdropFilter: 'blur(4px)', boxShadow: '0 0 10px rgba(0, 243, 255, 0.1)' }}
              title="Reset view"
            >
              [ RECALIBRATE ]
            </button>
          </div>

          {/* Hover detail */}
          {hoverNode && (
            <div
              className="absolute bottom-6 left-6 p-4 rounded-xl max-w-sm font-mono"
              style={{ 
                background: 'rgba(2, 6, 23, 0.85)', 
                border: `1px solid ${hoverNode.source === 'auto' ? AUTO : YOU}`, 
                boxShadow: `0 0 20px ${hoverNode.source === 'auto' ? AUTO : YOU}40`,
                backdropFilter: 'blur(8px)',
                zIndex: 50
              }}
            >
              <div className="flex items-center gap-3 mb-3 pb-2" style={{ borderBottom: `1px solid rgba(${hoverNode.source === 'auto' ? '255, 176, 0' : '0, 243, 255'}, 0.3)` }}>
                <Dot color={hoverNode.source === 'auto' ? AUTO : YOU} />
                <span className="font-bold text-sm tracking-wider" style={{ color: '#fff', textShadow: `0 0 8px ${hoverNode.source === 'auto' ? AUTO : YOU}` }}>
                  {hoverNode.title.toUpperCase()}
                </span>
              </div>
              
              <div className="text-xs mb-3 leading-relaxed min-h-[40px]" style={{ color: 'rgba(255, 255, 255, 0.7)' }}>
                {hoverDetail?.slug === hoverNode.slug ? hoverDetail.summary : 'INITIALIZING SCAN...'}
              </div>

              <div className="text-[10px] uppercase tracking-widest flex flex-col gap-1.5" style={{ color: hoverNode.source === 'auto' ? AUTO : YOU }}>
                <div className="flex justify-between">
                  <span className="opacity-70">TOPOLOGY:</span>
                  <span>{hoverNode.degree} CONNECTION{hoverNode.degree === 1 ? '' : 'S'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="opacity-70">VECTORS:</span>
                  <span>{hoverNode.tags.length > 0 ? hoverNode.tags.join(' // ') : 'NONE'}</span>
                </div>
              </div>
            </div>
          )}

          <div className="absolute bottom-4 right-4 text-[10px] font-mono tracking-widest" style={{ color: 'rgba(0, 243, 255, 0.5)' }}>
            DRAG NODES // SCROLL ZOOM // PAN CANVAS
          </div>
        </div>
      </div>
    </div>
  )
}

function Dot({ color }: { color: string }) {
  return <span className="inline-block w-2 h-2 rounded-full" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />
}
