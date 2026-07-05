import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { studiesApi } from '../api/client'
import { FileIcon, UploadIcon } from '../components/Icons'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

export default function NewProjectPage() {
  const [projectName, setProjectName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [progress, setProgress] = useState(0)
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'processing'>('idle')
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const acceptFile = (f: File | undefined) => {
    if (!f) return
    if (!/\.(sav|csv|xlsx)$/i.test(f.name)) {
      setError('Unsupported format. Upload SPSS (.sav), CSV, or Excel (.xlsx).')
      return
    }
    setError(null)
    setFile(f)
    if (!projectName) setProjectName(f.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' '))
  }

  const submit = async () => {
    if (!file) return
    setSubmitting(true)
    setError(null)
    setPhase('uploading')
    setProgress(0)
    try {
      const r = await studiesApi.upload(file, projectName.trim() || undefined, (pct) => {
        setProgress(pct)
        // The POST resolves once bytes are fully received; the orchestrator
        // then classifies/QCs/renders in a background thread. Once the
        // upload itself hits 100%, communicate that we're now waiting on
        // the pipeline rather than looking stuck at "100%".
        if (pct >= 100) setPhase('processing')
      })
      navigate(`/projects/${r.data.study_id}`)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Upload failed. Is the API server running?')
      setSubmitting(false)
      setPhase('idle')
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-slate-900 mb-1">New Project</h1>
      <p className="text-sm text-slate-500 mb-8">
        Upload survey data — the pipeline runs intake, QC, coding, tables, and reports automatically.
      </p>

      <div className="bg-white rounded-lg border border-slate-200 p-6 space-y-6">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">Project name</label>
          <input
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            placeholder="e.g. PharmaCo Brand Tracker W3"
            className="w-full h-10 px-3 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">Data file</label>
          <div
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              acceptFile(e.dataTransfer.files?.[0])
            }}
            className={`border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition-colors ${
              dragging ? 'border-sky-500 bg-sky-50' : 'border-slate-300 hover:border-slate-400 bg-slate-50/50'
            }`}
          >
            {file ? (
              <div className="flex items-center justify-center gap-3">
                <FileIcon className="w-8 h-8 text-primary-500" />
                <div className="text-left">
                  <p className="font-medium text-slate-800 text-sm">{file.name}</p>
                  <p className="text-xs text-slate-500">{formatBytes(file.size)} — click to change</p>
                </div>
              </div>
            ) : (
              <>
                <UploadIcon className="w-8 h-8 text-slate-400 mx-auto mb-3" />
                <p className="text-sm font-medium text-slate-700">Drop your data file here, or click to browse</p>
                <p className="text-xs text-slate-500 mt-1">SPSS (.sav) · CSV · Excel (.xlsx)</p>
              </>
            )}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".sav,.csv,.xlsx"
            className="hidden"
            onChange={(e) => acceptFile(e.target.files?.[0])}
          />
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3 rounded-md">{error}</div>
        )}

        {phase !== 'idle' && (
          <div>
            <div className="flex items-center justify-between text-xs text-slate-600 mb-1.5">
              <span>
                {phase === 'uploading'
                  ? `Uploading… ${progress}%`
                  : 'Upload complete — starting pipeline…'}
              </span>
              {phase === 'uploading' && file && file.size > 20 * 1024 ** 2 && (
                <span className="text-slate-400">Large file — this may take a while</span>
              )}
            </div>
            <div className="h-1.5 rounded-full bg-slate-200 overflow-hidden">
              <div
                className={`h-full bg-primary-500 transition-all duration-200 ${
                  phase === 'processing' ? 'animate-pulse' : ''
                }`}
                style={{ width: `${phase === 'processing' ? 100 : progress}%` }}
              />
            </div>
          </div>
        )}

        <div className="flex items-center justify-between pt-2">
          <p className="text-xs text-slate-500">
            Pipeline: intake → QC → coding → tables → Excel + PowerPoint
          </p>
          <button
            onClick={submit}
            disabled={!file || submitting}
            className="bg-primary-500 text-white text-sm font-medium px-5 py-2.5 rounded-md hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? 'Please wait…' : 'Create project'}
          </button>
        </div>
      </div>
    </div>
  )
}
