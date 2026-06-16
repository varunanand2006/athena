import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'

type DocumentStatus = 'processing' | 'complete' | 'failed'

interface Document {
  id: string
  filename: string
  title: string
  doc_type: string
  summary: string
  chunk_count: number
  size_bytes: number
  status: DocumentStatus
  added_at: string | null
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString()
}

export default function DocumentsView() {
  const [docs, setDocs] = useState<Document[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [hovering, setHovering] = useState(false)
  const [uploading, setUploading] = useState<string[]>([])
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const fetchDocs = useCallback(async () => {
    try {
      setError(false)
      const res = await axios.get<Document[]>('/documents', { timeout: 10_000 })
      setDocs(res.data)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchDocs()
  }, [fetchDocs])

  // Poll while any document is still being processed. Stop once every
  // row has settled into 'complete' or 'failed' — no point refetching.
  useEffect(() => {
    const stillProcessing = docs.some((d) => d.status === 'processing')
    if (!stillProcessing) return
    const id = setInterval(fetchDocs, 4000)
    return () => clearInterval(id)
  }, [docs, fetchDocs])

  const uploadFiles = useCallback(
    async (files: FileList | File[]) => {
      setUploadError(null)
      const list = Array.from(files)
      if (list.length === 0) return
      setUploading((prev) => [...prev, ...list.map((f) => f.name)])
      try {
        for (const file of list) {
          const fd = new FormData()
          fd.append('file', file)
          try {
            // /ingest returns fast — server kicks embedding/summary into a
            // background thread. Polling picks up chunk_count/summary later.
            await axios.post('/ingest', fd, { timeout: 60_000 })
          } catch (e) {
            setUploadError(`Failed to start ingest for ${file.name}`)
          } finally {
            setUploading((prev) => prev.filter((n) => n !== file.name))
          }
        }
      } finally {
        await fetchDocs()
      }
    },
    [fetchDocs]
  )

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setHovering(false)
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        uploadFiles(e.dataTransfer.files)
      }
    },
    [uploadFiles]
  )

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setHovering(true)
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setHovering(false)
  }, [])

  const onPickFiles = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        uploadFiles(e.target.files)
      }
      e.target.value = ''
    },
    [uploadFiles]
  )

  const onDelete = useCallback(
    async (doc: Document) => {
      if (!window.confirm(`Delete "${doc.title}"? This removes the file, catalog row, and search index.`)) {
        return
      }
      setDeletingId(doc.id)
      try {
        await axios.delete(`/ingest/documents/${doc.id}`, { timeout: 30_000 })
        await fetchDocs()
      } catch {
        setUploadError(`Failed to delete ${doc.title}`)
      } finally {
        setDeletingId(null)
      }
    },
    [fetchDocs]
  )

  return (
    <div className="h-full overflow-y-auto bg-transparent">
      <div className="px-8 py-6 shrink-0 bg-transparent flex flex-col items-start z-10">
        <h1 className="font-bold text-3xl tracking-wide uppercase" style={{ color: 'var(--text)', textShadow: 'var(--glow)' }}>Documents</h1>
        <p className="text-sm mt-1 uppercase tracking-widest font-semibold" style={{ color: 'var(--text-muted)' }}>
          Knowledge Base
        </p>
      </div>
      <div className="px-8 pb-8 flex flex-col gap-6 z-10 max-w-5xl">
        {/* Drag-drop + button */}
        <div
          onDrop={onDrop}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          className={`rounded-2xl p-8 flex flex-col items-center justify-center gap-3 transition-colors ${hovering ? 'glass-card' : 'glass-panel'}`}
          style={{
            border: `2px dashed ${hovering ? 'var(--accent)' : 'var(--border)'}`,
            boxShadow: 'var(--shadow-md)',
          }}
        >
          <IconUpload />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="px-4 py-2 rounded-lg text-sm font-medium text-white"
            style={{ background: 'var(--accent)' }}
          >
            Upload file
          </button>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            …or drag files here. PDF, TXT, MD, DOCX
          </p>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={onPickFiles}
            style={{ display: 'none' }}
          />
        </div>

        {/* Upload status */}
        {uploading.length > 0 && (
          <div
            className="rounded-xl p-3 flex flex-col gap-1 glass-card"
            style={{ border: '1px solid var(--border)' }}
          >
            {uploading.map((name) => (
              <div key={name} className="flex items-center gap-2 text-sm" style={{ color: 'var(--text)' }}>
                <Spinner />
                Ingesting {name}…
              </div>
            ))}
          </div>
        )}
        {uploadError && (
          <p className="text-sm" style={{ color: 'var(--accent)' }}>{uploadError}</p>
        )}

        {/* Catalog table */}
        <div
          className="rounded-2xl overflow-hidden glass-card"
          style={{ boxShadow: 'var(--shadow-md)', border: '1px solid var(--border)' }}
        >
          <div className="px-5 py-3 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
            <h2 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>
              Library
            </h2>
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {docs.length} document{docs.length === 1 ? '' : 's'}
            </span>
          </div>

          {loading && (
            <p className="text-sm py-8 text-center" style={{ color: 'var(--text-muted)' }}>
              Loading…
            </p>
          )}
          {error && (
            <p className="text-sm py-8 text-center" style={{ color: 'var(--accent)' }}>Failed to load documents</p>
          )}
          {!loading && !error && docs.length === 0 && (
            <p className="text-sm py-8 text-center" style={{ color: 'var(--text-muted)' }}>
              No documents yet — upload one above.
            </p>
          )}
          {!loading && !error && docs.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr style={{ background: 'var(--bg)' }}>
                  <th className="text-left px-5 py-2 font-medium text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                    Title
                  </th>
                  <th className="text-left px-3 py-2 font-medium text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                    Type
                  </th>
                  <th className="text-left px-3 py-2 font-medium text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                    Added
                  </th>
                  <th className="text-left px-3 py-2 font-medium text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                    Size
                  </th>
                  <th className="text-left px-5 py-2 font-medium text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
                    Summary
                  </th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td className="px-5 py-3 align-top">
                      <div className="font-medium" style={{ color: 'var(--text)' }}>{d.title}</div>
                      <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{d.filename}</div>
                    </td>
                    <td className="px-3 py-3 align-top text-xs uppercase" style={{ color: 'var(--text-muted)' }}>
                      {d.doc_type}
                    </td>
                    <td className="px-3 py-3 align-top text-xs" style={{ color: 'var(--text-muted)' }}>
                      {formatDate(d.added_at)}
                    </td>
                    <td className="px-3 py-3 align-top text-xs" style={{ color: 'var(--text-muted)' }}>
                      {formatBytes(d.size_bytes)}
                    </td>
                    <td className="px-5 py-3 align-top text-xs" style={{ color: 'var(--text)' }}>
                      {d.status === 'processing' ? (
                        <span className="inline-flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }}>
                          <Spinner />
                          Processing…
                        </span>
                      ) : d.status === 'failed' ? (
                        <span className="inline-flex items-center gap-1.5">
                          <span
                            className="inline-block px-1.5 py-0.5 rounded text-xs font-medium"
                            style={{ background: 'rgba(30,144,255,0.1)', color: 'var(--accent)' }}
                          >
                            Failed
                          </span>
                          <span style={{ color: 'var(--text-muted)' }}>
                            Delete and re-upload to retry
                          </span>
                        </span>
                      ) : (
                        d.summary || <span style={{ color: 'var(--text-muted)' }}>—</span>
                      )}
                    </td>
                    <td className="px-3 py-3 align-top text-right">
                      <button
                        type="button"
                        onClick={() => onDelete(d)}
                        disabled={deletingId === d.id}
                        title={d.status === 'failed' ? 'Delete and re-upload to retry' : 'Delete document'}
                        className="p-1.5 rounded transition-colors disabled:opacity-50 hover:bg-[var(--accent-light)]"
                        style={{ color: 'var(--accent)' }}
                      >
                        {deletingId === d.id ? <Spinner /> : <IconTrash />}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

function IconTrash() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  )
}

function IconUpload() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--accent)' }}>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  )
}

function Spinner() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="animate-spin"
      style={{ color: 'var(--accent)' }}
    >
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  )
}
