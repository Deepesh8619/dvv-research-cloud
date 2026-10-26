import { useCallback, useEffect, useState } from 'react'

import LedgerStrip from '../components/LedgerStrip'
import AdverseEventsPanel from '../components/AdverseEventsPanel'
import CleaningPanel from '../components/CleaningPanel'
import CrosscheckPanel from '../components/CrosscheckPanel'
import axios from 'axios'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Study, studiesApi, shareApi, ShareLink, questionnaireApi } from '../api/client'
import FieldworkCards from '../components/FieldworkCards'
import LifecycleRail from '../components/LifecycleRail'
import FindingsPanel from '../components/FindingsPanel'
import ReportPanel from '../components/ReportPanel'
import TablesPanel from '../components/TablesPanel'
import TrackerPanel from '../components/TrackerPanel'
import MethodsPanel from '../components/MethodsPanel'
import LogicMap from '../components/LogicMap'
import VariablesPanel from '../components/VariablesPanel'
import NoticePanel from '../components/NoticePanel'
import ProjectDataBar, { DATA_CHANGED } from '../components/ProjectDataBar'
import OpenEndsPanel from '../components/OpenEndsPanel'
import ProbesPanel from '../components/ProbesPanel'
import QuotePanel from '../components/QuotePanel'
import TranslationsPanel from '../components/TranslationsPanel'
import { useAuth } from '../auth/AuthContext'
import ChatBot from '../components/ChatBot'
import { CheckIcon, DocChartIcon, DownloadIcon, LinkIcon } from '../components/Icons'
import LoadingSpinner from '../components/LoadingSpinner'
import StatusPill from '../components/StatusPill'
import { canWrite as hasWriteRole } from '../auth/roles'
import PageHeader from '../components/ui/PageHeader'
import { buttonClasses } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import EmptyState from '../components/ui/EmptyState'

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

// Crosstabs no longer auto-display as a nav tab — the Overview tab's Data
// Profile card is the default view instead. Crosstabs stay fully available
// on demand via the chat navigator's show_table tool (rendered inline in
// ChatBot), and CrosstabTable/studiesApi.crosstabs remain wired for that.
// "Questionnaire checks" is its own tab, not a card on Overview. The QC panel
// below it validates a data file on its OWN terms — speeders, empty columns,
// straightliners — which says nothing about whether the data matches the
// instrument the client signed off. That second comparison is the one a DP shop
// is paid for, and burying it under generic data QC misrepresented which of the
// two matters.
const TABS = ['Overview', 'Questionnaire checks', 'Logic map', 'Variables', 'Reports', 'Cleaning', 'Open ends', 'Activity'] as const
// The full variable list, hidden ones included, is for the people who build and
// check the data — not for client reviewers.
const STAFF_ONLY_TABS: ReadonlyArray<string> = ['Variables']
type Tab = (typeof TABS)[number]

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const tab = (params.get('tab') as Tab) || 'Overview'
  const { user } = useAuth()
  const isStaff = user?.role === 'owner' || user?.role === 'analyst'

  const [study, setStudy] = useState<Study | null>(null)
  const [loading, setLoading] = useState(true)
  const [approving, setApproving] = useState(false)
  const [shareLink, setShareLink] = useState<ShareLink | null>(null)
  const [showShareModal, setShowShareModal] = useState(false)
  const [sharing, setSharing] = useState(false)
  const [reprocessing, setReprocessing] = useState(false)
  const [reprocessError, setReprocessError] = useState<string | null>(null)
  // null = unknown/loading; true = study has no questionnaire yet (offer builder).
  const [noQuestionnaire, setNoQuestionnaire] = useState<boolean | null>(null)

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

  // New data uploaded in the data bar: every tab reads it, so reload the project.
  useEffect(() => {
    window.addEventListener(DATA_CHANGED, load)
    return () => window.removeEventListener(DATA_CHANGED, load)
  }, [load])

  // Is there a questionnaire yet? A 404 means none — offer the "Build survey"
  // entry point. Any other error leaves it unknown (button simply not shown).
  useEffect(() => {
    if (!id) return
    questionnaireApi
      .status(id)
      .then(() => setNoQuestionnaire(false))
      .catch((err) => setNoQuestionnaire(axios.isAxiosError(err) && err.response?.status === 404))
  }, [id])

  // Poll while processing
  useEffect(() => {
    // 'authoring' is a data-less survey-first study — nothing is processing in
    // the background, so don't poll (it would spin forever with no change).
    if (!study || ['delivered', 'error', 'archived', 'approved_for_delivery', 'rendered', 'authoring'].includes(study.status)) return
    const t = setInterval(load, 2500)
    return () => clearInterval(t)
  }, [study?.status, load])

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

  const handleReprocess = async () => {
    if (!id || reprocessing) return
    if (!window.confirm('Re-run the full pipeline for this project? Tables and decks will be regenerated.')) return
    setReprocessing(true)
    setReprocessError(null)
    try {
      await studiesApi.reprocess(id)
      load()
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
      setReprocessError(typeof detail === 'string' ? detail : 'Re-process failed')
    } finally {
      setReprocessing(false)
    }
  }

  const handleNavigate = useCallback((targetTab: string) => {
    // tableId (crosstab selection) is no longer routed through a Tables tab —
    // the chat navigator renders the requested crosstab inline via show_table,
    // so navigate_to only needs to switch between the remaining tabs now.
    if (TABS.includes(targetTab as Tab)) {
      setTab(targetTab as Tab)
    }
  }, [])

  if (loading) return <LoadingSpinner />
  if (!study) {
    return (
      <div className="text-center py-20 text-slate-500">
        Project not found. <Link to="/projects" className="text-sky-600">Back to projects</Link>
      </div>
    )
  }

  const progress = STAGE_PROGRESS[study.status] ?? 0
  // A survey-first study with no responses yet: the analysis tabs have nothing
  // to show. Once it's fielded + materialized (or data is uploaded), excel_path
  // is set and status leaves 'authoring', so the normal tabs light up.
  const noData = study.status === 'authoring' && !study.excel_path
  const deliveryApproved = study.approval_chain.some(
    (a) => a.gate === 'delivery' && a.approved_by !== 'pending',
  )

  return (
    <div>
      {/* Header */}
      <Link to="/projects" className="mb-2 inline-block text-sm text-slate-500 hover:text-slate-700">
        ← Projects
      </Link>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-3">
            {study.client_name}
            <StatusPill status={study.status} />
          </span>
        }
        description={<span className="font-mono text-xs text-slate-400">{study.id}</span>}
        icon={DocChartIcon}
        actions={
          <>
            {hasWriteRole(user?.role) && (
              <button
                onClick={handleReprocess}
                disabled={reprocessing}
                title="Re-run the full pipeline from the original file (recovers a study after a server restart; picks up new QC/coding features)"
                className={buttonClasses('secondary')}
              >
                {reprocessing ? 'Starting…' : 'Re-process'}
              </button>
            )}
            {noQuestionnaire && hasWriteRole(user?.role) && (
              <Link to={`/projects/${study.id}/builder`} className={buttonClasses('secondary')}>
                Build survey
              </Link>
            )}
            <Link to={`/projects/${study.id}/questionnaire`} className={buttonClasses('secondary')}>
              Questionnaire
            </Link>
            <Link to={`/projects/${study.id}/fielding`} className={buttonClasses('secondary')}>
              Fielding
            </Link>
            {study.excel_path && (
              <button
                type="button"
                onClick={() => studiesApi.download(study.id, 'excel', `${study.client_name}.xlsx`)}
                className={buttonClasses('secondary')}
              >
                <DownloadIcon className="w-4 h-4" /> Excel
              </button>
            )}
            {study.pptx_path && (
              <button
                type="button"
                onClick={() => studiesApi.download(study.id, 'pptx', `${study.client_name}.pptx`)}
                className={buttonClasses('secondary')}
              >
                <DownloadIcon className="w-4 h-4" /> PowerPoint
              </button>
            )}
            {study.status === 'approved_for_delivery' && !deliveryApproved && user?.role === 'owner' && (
              <button
                onClick={handleApprove}
                disabled={approving}
                className={buttonClasses('success')}
              >
                <CheckIcon className="w-4 h-4" />
                {approving ? 'Signing off…' : 'Sign off for delivery'}
              </button>
            )}
            {study.status === 'approved_for_delivery' && deliveryApproved && hasWriteRole(user?.role) && (
              <button onClick={() => setShowShareModal(true)} className={buttonClasses('secondary')}>
                <LinkIcon className="w-4 h-4" /> Share
              </button>
            )}
          </>
        }
      />
      {reprocessError && (
        <p className="mb-4 -mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
          {reprocessError}
        </p>
      )}

      <LifecycleRail
        studyId={study.id}
        onOpen={(t) => (t.startsWith('/') ? navigate(`/projects/${study.id}${t}`) : setTab(t as Tab))}
      />

      <ProjectDataBar studyId={study.id} canWrite={hasWriteRole(user?.role)} />

      {/* Tab nav */}
      <div className="border-b border-slate-200 mb-6">
        <nav className="flex gap-1 overflow-x-auto">
          {TABS.filter((t) => !STAFF_ONLY_TABS.includes(t) || isStaff).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-t-sm px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40 ${
                tab === t
                  ? 'border-primary-500 text-primary-500'
                  : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              {t}
            </button>
          ))}
        </nav>
      </div>

      {tab === 'Overview' && (noData ? <NoDataState /> : <OverviewTab study={study} progress={progress} />)}
      {tab === 'Questionnaire checks' && (
        <QuestionnaireChecksTab study={study} canWrite={hasWriteRole(user?.role)} />
      )}
      {tab === 'Reports' && (noData ? <NoDataState /> : <ReportsTab study={study} />)}
      {tab === 'Cleaning' && (noData ? <NoDataState /> : (
        <div className="space-y-4">
          {hasWriteRole(user?.role) && <AdverseEventsPanel studyId={study.id} />}
          <CleaningPanel studyId={study.id} role={user?.role} />
        </div>
      ))}
      {tab === 'Open ends' && (noData ? <NoDataState /> : <OpenEndsPanel studyId={study.id} />)}
      {tab === 'Activity' && <ActivityTab study={study} />}
      {tab === 'Logic map' && <LogicMap studyId={study.id} canWrite={hasWriteRole(user?.role)} />}
      {tab === 'Variables' && isStaff && <VariablesPanel studyId={study.id} />}

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

function NoDataState() {
  return (
    <Card>
      <EmptyState
        icon={DocChartIcon}
        title="No data yet"
        description={
          <>
            Field this survey with the{' '}
            <span className="font-medium text-slate-700">Fielding</span> link above, or, if the
            study was fielded elsewhere, upload its data file in the{' '}
            <span className="font-medium text-slate-700">Questionnaire checks</span> tab.
          </>
        }
      />
    </Card>
  )
}

function QuestionnaireChecksTab({
  study,
  canWrite,
}: {
  study: Study
  canWrite: boolean
}) {
  if (!canWrite) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 shadow-panel p-8 text-center">
        <p className="text-sm text-slate-500">
          Running the cross-check needs an owner, analyst or founder account.
        </p>
      </div>
    )
  }
  return (
    <div className="space-y-4">
      <NoticePanel studyId={study.id} />
      <QuotePanel studyId={study.id} />
      <TranslationsPanel studyId={study.id} />
      <ProbesPanel studyId={study.id} />
      <CrosscheckPanel studyId={study.id} dataFilename={study.source_filename} />
    </div>
  )
}


function OverviewTab({ study, progress }: { study: Study; progress: number }) {
  return (
    <div className="grid grid-cols-3 gap-6">
      <div className="col-span-2 space-y-6">
        {/* Pipeline */}
        <div className="bg-white rounded-lg border border-slate-200 shadow-panel p-6">
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

        {/* What the fieldwork produced, read from the data as fieldwork —
            not a column-by-column profile of a spreadsheet. */}
        <FieldworkCards studyId={study.id} ready={!!study.excel_path} />

        {/* QC */}
        <div className="bg-white rounded-lg border border-slate-200 shadow-panel p-6">
          <h2 className="font-semibold text-slate-900 mb-4">Quality control</h2>
          {study.metadata.qc_passed == null ? (
            /* "QC pending…" was shown whether the pipeline was two seconds in,
               twenty minutes in, or dead — the one thing it never said was
               which. The stage is a heartbeat, so naming it distinguishes
               working from stopped without anyone reading a server log. */
            <p className="text-sm text-slate-500">
              {study.metadata.stage
                ? `Running: ${study.metadata.stage}…`
                : 'Quality checks run once the data file has been read.'}
            </p>
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
      <div className="bg-white rounded-lg border border-slate-200 shadow-panel p-6 h-fit">
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
      desc: 'Client-ready deck: key findings first, then one slide per table; every number cited to its table and base.',
      fmt: 'pptx' as const,
      available: !!study.pptx_path,
    },
  ]
  return (
    <div className="space-y-6">
      <ReportPanel studyId={study.id} />
      <FindingsPanel studyId={study.id} />
      <TablesPanel studyId={study.id} />
      <TrackerPanel study={study} />
      <MethodsPanel studyId={study.id} />
      <div className="grid grid-cols-2 gap-6">
        {items.map((item) => (
          <div key={item.fmt} className="bg-white rounded-lg border border-slate-200 shadow-panel p-6">
            <h2 className="font-semibold text-slate-900 mb-1">{item.title}</h2>
            <p className="text-sm text-slate-500 mb-5">{item.desc}</p>
            {item.available ? (
              <button
                type="button"
                onClick={() =>
                  studiesApi.download(
                    study.id,
                    item.fmt,
                    `${study.client_name}.${item.fmt === 'excel' ? 'xlsx' : 'pptx'}`,
                  )
                }
                className="inline-flex items-center gap-2 bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600"
              >
                <DownloadIcon className="w-4 h-4" /> Download
              </button>
            ) : (
              <span className="text-sm text-slate-400">Not yet generated</span>
            )}
          </div>
        ))}
      </div>
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
    <>
    <LedgerStrip studyId={study.id} />
    <div className="bg-white rounded-lg border border-slate-200 shadow-panel p-6 max-w-2xl">
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
    </>
  )
}
