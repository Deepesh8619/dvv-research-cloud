import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { CrosstabsPayload, Study, studiesApi, shareApi, ShareLink } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import ChatBot from '../components/ChatBot'
import CrosstabTable from '../components/CrosstabTable'
import { CheckIcon, DownloadIcon, LinkIcon } from '../components/Icons'
import LoadingSpinner from '../components/LoadingSpinner'
import StatusPill from '../components/StatusPill'

const PIPELINE = [
  { key: 'classify', label: 'Intake' },
  { key: 'qc', label: 'Quality Check' },
  { key: 'coding', label: 'Coding' },
  { key: 'tables', label: 'Tables' },
  { key: 'render', label: 'Reports' },
  { key: 'delivery', label: 'Delivery' },
]

const STAGE_PROGRESS: Record<string, number> = {
  classifying: 1,
  qc_pending: 1,
  qc_passed: 2,
  coding_done: 3,
  rendered: 5,
  approved_for_delivery: 5,
  delivered: 6,
  error: 0,
}

const TABS = ['Overview', 'Tables', 'Reports', 'Activity'] as const
type Tab = (typeof TABS)[number]

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab) || 'Overview'
  const { user } = useAuth()

  const [study, setStudy] = useState<Study | null>(null)
  const [tabs, setTabs] = useState<CrosstabsPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [approving, setApproving] = useState(false)
  const [shareLink, setShareLink] = useState<ShareLink | null>(null)
  const [showShareModal, setShowShareModal] = useState(false)
  const [sharing, setSharing] = useState(false)

  const load = useCallback(() => {
    if (!id) return
    studiesApi
      .get(id)
      .then((r) => setStudy(r.data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [id])

  useEffect(() => {
    load()
  }, [load])

  // Poll while processing
  useEffect(() => {
    if (!study || ['delivered', 'error', 'archived', 'approved_for_delivery', 'rendered'].includes(study.status)) return
    const t = setInterval(load, 2500)
    return () => clearInterval(t)
  }, [study?.status, load])

  // Load table data when available
  useEffect(() => {
    if (!id || !study?.excel_path || tabs) return
    studiesApi
      .crosstabs(id)
      .then((r) => setTabs(r.data))
      .catch(() => {})
  }, [id, study?.excel_path, tabs])

  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true })

  const handleApprove = async () => {
    if (!id) return
    setApproving(true)
    try {
      await studiesApi.approve(id, 'delivery')
      load()
    } finally {
      setApproving(false)
    }
  }

  const handleCreateShareLink = async () => {
    if (!id) return
    setSharing(true)
    try {
      const res = await shareApi.create(id)
      setShareLink(res.data)
    } finally {
      setSharing(false)
    }
  }

  const handleRevokeShareLink = async () => {
    if (!id) return
    setSharing(true)
    try {
      await shareApi.revoke(id)
      setShareLink(null)
      setShowShareModal(false)
    } finally {
      setSharing(false)
    }
  }

  const handleNavigate = useCallback((targetTab: string, tableId?: string) => {
    if (TABS.includes(targetTab as Tab)) {
      setTab(targetTab as Tab)
    }
    if (tableId && tabs) {
      // Find and select the table by ID
      const idx = tabs.tables.findIndex((t) => t.id === tableId)
      if (idx >= 0) {
        // We would need to pass a setter to TablesTab, but for now just switch tabs
        // The user can manually select the table
      }
    }
  }, [tabs])

  if (loading) return <LoadingSpinner />
  if (!study) {
    return (
      <div className="text-center py-20 text-slate-500">
        Project not found. <Link to="/projects" className="text-sky-600">Back to projects</Link>
      </div>
    )
  }

  const progress = STAGE_PROGRESS[study.status] ?? 0
  const deliveryApproved = study.approval_chain.some(
    (a) => a.gate === 'delivery' && a.approved_by !== 'pending',
  )

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link to="/projects" className="text-sm text-slate-500 hover:text-slate-700">
          Projects /
        </Link>
        <div className="flex items-center justify-between mt-1">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold text-slate-900">{study.client_name}</h1>
            <StatusPill status={study.status} />
          </div>
          <div className="flex items-center gap-2">
            {study.excel_path && (
              <a
                href={studiesApi.downloadUrl(study.id, 'excel')}
                className="inline-flex items-center gap-2 border border-slate-300 bg-white text-slate-700 text-sm font-medium px-3.5 py-2 rounded-md hover:bg-slate-50"
              >
                <DownloadIcon className="w-4 h-4" /> Excel
              </a>
            )}
            {study.pptx_path && (
              <a
                href={studiesApi.downloadUrl(study.id, 'pptx')}
                className="inline-flex items-center gap-2 border border-slate-300 bg-white text-slate-700 text-sm font-medium px-3.5 py-2 rounded-md hover:bg-slate-50"
              >
                <DownloadIcon className="w-4 h-4" /> PowerPoint
              </a>
            )}
            {study.status === 'approved_for_delivery' && !deliveryApproved && user?.role === 'owner' && (
              <button
                onClick={handleApprove}
                disabled={approving}
                className="inline-flex items-center gap-2 bg-emerald-600 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-emerald-700 disabled:opacity-60"
              >
                <CheckIcon className="w-4 h-4" />
                {approving ? 'Signing off…' : 'Sign off for delivery'}
              </button>
            )}
            {study.status === 'approved_for_delivery' && deliveryApproved && (user?.role === 'owner' || user?.role === 'analyst') && (
              <button
                onClick={() => setShowShareModal(true)}
                className="inline-flex items-center gap-2 border border-slate-300 bg-white text-slate-700 text-sm font-medium px-3.5 py-2 rounded-md hover:bg-slate-50"
              >
                <LinkIcon className="w-4 h-4" /> Share
              </button>
            )}
          </div>
        </div>
        <p className="text-xs text-slate-400 font-mono mt-1">{study.id}</p>
      </div>

      {/* Tab nav */}
      <div className="border-b border-slate-200 mb-6">
        <nav className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
                tab === t
                  ? 'border-primary-500 text-primary-500'
                  : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              {t}
              {t === 'Tables' && tabs ? ` (${tabs.tables.length})` : ''}
            </button>
          ))}
        </nav>
      </div>

      {tab === 'Overview' && <OverviewTab study={study} progress={progress} />}
      {tab === 'Tables' && <TablesTab tabs={tabs} ready={!!study.excel_path} />}
      {tab === 'Reports' && <ReportsTab study={study} />}
      {tab === 'Activity' && <ActivityTab study={study} />}

      {/* Share modal */}
      {showShareModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg max-w-md w-full shadow-lg">
            <div className="px-6 py-4 border-b border-slate-200">
              <h2 className="font-semibold text-slate-900">Share with client</h2>
            </div>
            <div className="px-6 py-4 space-y-4">
              {!shareLink ? (
                <>
                  <p className="text-sm text-slate-600">
                    Generate a shareable link to send to your client. They'll be able to download the approved report without needing an account.
                  </p>
                  <button
                    onClick={handleCreateShareLink}
                    disabled={sharing}
                    className="w-full px-4 py-2 bg-primary-500 text-white text-sm font-medium rounded-md hover:bg-primary-600 disabled:opacity-50"
                  >
                    {sharing ? 'Generating…' : 'Generate share link'}
                  </button>
                </>
              ) : (
                <>
                  <p className="text-sm text-slate-600">Share this link with your client:</p>
                  <div className="bg-slate-50 rounded-md p-3 border border-slate-200">
                    <p className="text-xs text-slate-600 break-all font-mono">
                      {window.location.origin}/share/{shareLink.token}
                    </p>
                  </div>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(`${window.location.origin}/share/${shareLink.token}`)
                    }}
                    className="w-full px-4 py-2 border border-slate-300 text-slate-700 text-sm font-medium rounded-md hover:bg-slate-50"
                  >
                    Copy link
                  </button>
                  <button
                    onClick={handleRevokeShareLink}
                    disabled={sharing}
                    className="w-full px-4 py-2 border border-red-300 text-red-700 text-sm font-medium rounded-md hover:bg-red-50 disabled:opacity-50"
                  >
                    {sharing ? 'Revoking…' : 'Revoke link'}
                  </button>
                </>
              )}
            </div>
            <div className="px-6 py-4 border-t border-slate-200">
              <button
                onClick={() => setShowShareModal(false)}
                className="w-full px-4 py-2 text-slate-700 text-sm font-medium hover:bg-slate-50 rounded-md"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
      {/* ChatBot — project-scoped AI assistant */}
      <ChatBot
        studyId={id!}
        clientName={study.client_name}
        onNavigate={handleNavigate}
      />
    </div>
  )
}

function OverviewTab({ study, progress }: { study: Study; progress: number }) {
  return (
    <div className="grid grid-cols-3 gap-6">
      <div className="col-span-2 space-y-6">
        {/* Pipeline */}
        <div className="bg-white rounded-lg border border-slate-200 p-6">
          <h2 className="font-semibold text-slate-900 mb-5">Pipeline</h2>
          <div className="flex items-center">
            {PIPELINE.map((step, i) => (
              <div key={step.key} className="flex items-center flex-1 last:flex-none">
                <div className="flex flex-col items-center">
                  <div
                    className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold ${
                      i < progress
                        ? 'bg-emerald-500 text-white'
                        : i === progress
                          ? 'bg-amber-400 text-white animate-pulse'
                          : 'bg-slate-200 text-slate-500'
                    }`}
                  >
                    {i < progress ? <CheckIcon className="w-4 h-4" /> : i + 1}
                  </div>
                  <span className="text-[11px] text-slate-500 mt-1.5 whitespace-nowrap">{step.label}</span>
                </div>
                {i < PIPELINE.length - 1 && (
                  <div className={`h-0.5 flex-1 mx-2 mb-5 ${i < progress - 1 ? 'bg-emerald-400' : 'bg-slate-200'}`} />
                )}
              </div>
            ))}
          </div>
        </div>

        {/* QC */}
        <div className="bg-white rounded-lg border border-slate-200 p-6">
          <h2 className="font-semibold text-slate-900 mb-4">Quality control</h2>
          {study.metadata.qc_passed == null ? (
            <p className="text-sm text-slate-500">QC pending…</p>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-3">
                <span
                  className={`inline-flex items-center gap-1.5 text-sm font-medium ${
                    study.metadata.qc_passed ? 'text-emerald-700' : 'text-red-700'
                  }`}
                >
                  <CheckIcon className="w-4 h-4" />
                  {study.metadata.qc_passed ? 'All checks passed' : 'Issues found'}
                </span>
              </div>
              {(study.metadata.qc_issues ?? []).map((issue, i) => (
                <p key={i} className="text-sm text-red-600 mb-1">• {issue}</p>
              ))}
              {(study.metadata.qc_warnings ?? []).map((w, i) => (
                <p key={i} className="text-sm text-amber-600 mb-1">• {w}</p>
              ))}
            </>
          )}
          {study.metadata.error && (
            <p className="text-sm text-red-600 mt-2 font-mono bg-red-50 rounded p-3">{study.metadata.error}</p>
          )}
        </div>
      </div>

      {/* Meta sidebar */}
      <div className="bg-white rounded-lg border border-slate-200 p-6 h-fit">
        <h2 className="font-semibold text-slate-900 mb-4">Details</h2>
        <dl className="space-y-4 text-sm">
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">Created</dt>
            <dd className="text-slate-800 mt-0.5">
              {new Date(study.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">Last updated</dt>
            <dd className="text-slate-800 mt-0.5">
              {new Date(study.updated_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
            </dd>
          </div>
          {study.metadata.classification_confidence != null && (
            <div>
              <dt className="text-slate-500 text-xs uppercase tracking-wide">File classification</dt>
              <dd className="text-slate-800 mt-0.5">
                Survey data · {(study.metadata.classification_confidence * 100).toFixed(0)}% confidence
              </dd>
            </div>
          )}
          <div>
            <dt className="text-slate-500 text-xs uppercase tracking-wide">Significance testing</dt>
            <dd className="text-slate-800 mt-0.5">Column proportions z-test, 95% confidence</dd>
          </div>
        </dl>
      </div>
    </div>
  )
}

function TablesTab({ tabs, ready }: { tabs: CrosstabsPayload | null; ready: boolean }) {
  const [selected, setSelected] = useState(0)
  const [search, setSearch] = useState('')
  const [showBases, setShowBases] = useState(true)

  const filtered = useMemo(() => {
    if (!tabs) return []
    const q = search.trim().toLowerCase()
    return tabs.tables
      .map((t, i) => ({ ...t, index: i }))
      .filter((t) => !q || t.title.toLowerCase().includes(q) || t.stub.toLowerCase().includes(q))
  }, [tabs, search])

  if (!ready) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 p-12 text-center text-slate-500">
        Tables will appear here once processing completes.
      </div>
    )
  }
  if (!tabs) return <LoadingSpinner />

  const table = tabs.tables[selected]

  return (
    <div className="flex gap-6">
      {/* Table list */}
      <div className="w-72 shrink-0">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter tables…"
          className="w-full h-9 px-3 mb-2 rounded-md bg-white border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
        />
        <div className="bg-white rounded-lg border border-slate-200 divide-y divide-slate-100 max-h-[560px] overflow-y-auto">
          {filtered.map((t) => (
            <button
              key={t.id}
              onClick={() => setSelected(t.index)}
              className={`block w-full text-left px-4 py-3 text-sm transition-colors ${
                t.index === selected ? 'bg-primary-50 text-primary-500 font-medium' : 'text-slate-700 hover:bg-slate-50'
              }`}
            >
              <span className="text-xs text-slate-400 font-mono mr-2">{t.id.toUpperCase()}</span>
              {t.title}
            </button>
          ))}
        </div>
      </div>

      {/* Table display */}
      <div className="flex-1 min-w-0">
        <div className="bg-white rounded-lg border border-slate-200 p-6">
          <div className="flex items-start justify-between mb-5">
            <div>
              <h2 className="font-semibold text-slate-900">{table.title}</h2>
              <p className="text-xs text-slate-500 mt-1">
                {tabs.weight ? `Weighted by ${tabs.weight}` : 'Unweighted'} · {(tabs.confidence * 100).toFixed(0)}%
                confidence · letters mark columns significantly beaten
                {table.filter && ` · Filtered: ${table.filter}`}
              </p>
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-600 select-none cursor-pointer">
              <input
                type="checkbox"
                checked={showBases}
                onChange={(e) => setShowBases(e.target.checked)}
                className="rounded border-slate-300"
              />
              Show bases
            </label>
          </div>
          <CrosstabTable table={table} minBase={tabs.min_base_for_test} showBases={showBases} />
        </div>
      </div>
    </div>
  )
}

function ReportsTab({ study }: { study: Study }) {
  const items = [
    {
      title: 'Excel tab deck',
      desc: 'Full crosstab deck: one sheet per table, sig letters, nets, means, bases.',
      fmt: 'excel' as const,
      available: !!study.excel_path,
    },
    {
      title: 'PowerPoint report',
      desc: 'Client-ready deck: one slide per table with AI-drafted headline and insight.',
      fmt: 'pptx' as const,
      available: !!study.pptx_path,
    },
  ]
  return (
    <div className="grid grid-cols-2 gap-6">
      {items.map((item) => (
        <div key={item.fmt} className="bg-white rounded-lg border border-slate-200 p-6">
          <h2 className="font-semibold text-slate-900 mb-1">{item.title}</h2>
          <p className="text-sm text-slate-500 mb-5">{item.desc}</p>
          {item.available ? (
            <a
              href={studiesApi.downloadUrl(study.id, item.fmt)}
              className="inline-flex items-center gap-2 bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600"
            >
              <DownloadIcon className="w-4 h-4" /> Download
            </a>
          ) : (
            <span className="text-sm text-slate-400">Not yet generated</span>
          )}
        </div>
      ))}
    </div>
  )
}

function ActivityTab({ study }: { study: Study }) {
  const events = [...study.approval_chain].sort((a, b) => a.approved_at.localeCompare(b.approved_at))
  const gateLabels: Record<string, string> = {
    classify: 'File classification confirmed',
    qc: 'Quality checks approved',
    delivery: 'Delivery sign-off',
  }
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-6 max-w-2xl">
      <h2 className="font-semibold text-slate-900 mb-5">Approval history</h2>
      {events.length === 0 ? (
        <p className="text-sm text-slate-500">No activity yet.</p>
      ) : (
        <ol className="relative border-l border-slate-200 ml-2 space-y-6">
          {events.map((e) => (
            <li key={e.gate} className="pl-6 relative">
              <span
                className={`absolute -left-[7px] top-1 w-3.5 h-3.5 rounded-full border-2 border-white ${
                  e.approved_by === 'pending' ? 'bg-amber-400' : 'bg-emerald-500'
                }`}
              />
              <p className="text-sm font-medium text-slate-800">{gateLabels[e.gate] ?? e.gate}</p>
              <p className="text-xs text-slate-500 mt-0.5">
                {e.approved_by === 'pending' ? 'Awaiting review' : `Approved by ${e.approved_by}`} ·{' '}
                {new Date(e.approved_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
              </p>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
