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

const AUTO = '#f59e0b'   // synthesized (auto) notes — amber
const YOU = '#1e90ff'    // explicit (you) notes — accent blue

function radius(degree: number): number {
  return 5 + Math.sqrt(degree) * 2.6
}

export default function GraphView() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [, setTick] = useState(0)
  const [hover, setHover] = useState<string | null>(null)

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
          style={{ border: '1px solid var(--border)', boxShadow: 'var(--shadow-md)', cursor: 'grab' }}
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
                      stroke="var(--text-muted)"
                      strokeWidth={lit ? 1.4 : 0.7}
                      strokeOpacity={lit ? 0.5 : 0.12}
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
                        fillOpacity={lit ? 0.92 : 0.25}
                        stroke={n.slug === hover ? 'var(--text)' : color}
                        strokeWidth={n.slug === hover ? 2 : 0}
                      />
                      {showLabel && (
                        <text
                          x={r + 3}
                          y={3}
                          fontSize={10}
                          fill="var(--text)"
                          fillOpacity={lit ? 0.95 : 0.3}
                          style={{ pointerEvents: 'none', userSelect: 'none' }}
                        >
                          {n.title}
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
            <div className="absolute top-4 left-4 flex flex-col gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
              <div className="flex items-center gap-3 px-3 py-1.5 rounded-lg glass-card" style={{ border: '1px solid var(--border)' }}>
                <span className="flex items-center gap-1.5"><Dot color={YOU} /> you</span>
                <span className="flex items-center gap-1.5"><Dot color={AUTO} /> auto</span>
                <span>· {nodes.length} notes, {edges.length} links</span>
              </div>
            </div>
          )}

          <div className="absolute top-4 right-4 flex gap-2">
            <button
              onClick={resetView}
              className="px-2.5 py-1.5 rounded-lg text-xs font-medium glass-card transition-colors hover:opacity-80"
              style={{ border: '1px solid var(--border)', color: 'var(--text-muted)' }}
              title="Reset view"
            >
              Reset
            </button>
          </div>

          {/* Hover detail */}
          {hoverNode && (
            <div
              className="absolute bottom-4 left-4 px-3 py-2 rounded-lg glass-card max-w-xs"
              style={{ border: '1px solid var(--border)', boxShadow: 'var(--shadow-md)' }}
            >
              <div className="flex items-center gap-2">
                <Dot color={hoverNode.source === 'auto' ? AUTO : YOU} />
                <span className="font-semibold text-sm" style={{ color: 'var(--text)' }}>{hoverNode.title}</span>
              </div>
              <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {hoverNode.degree} link{hoverNode.degree === 1 ? '' : 's'}
                {hoverNode.tags.length > 0 ? ` · ${hoverNode.tags.join(', ')}` : ''}
              </div>
            </div>
          )}

          <div className="absolute bottom-4 right-4 text-[10px]" style={{ color: 'var(--text-muted)' }}>
            drag nodes · scroll to zoom · drag background to pan
          </div>
        </div>
      </div>
    </div>
  )
}

function Dot({ color }: { color: string }) {
  return <span className="inline-block w-2 h-2 rounded-full" style={{ background: color, boxShadow: `0 0 6px ${color}80` }} />
}
