import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import axios from 'axios'
import {
  Question,
  QuestionnaireStatus,
  QUESTION_TYPES,
  questionnaireApi,
  QuestionOption,
  UnresolvedQuestion,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import LoadingSpinner from '../components/LoadingSpinner'
import RoutingEditor, { FlatRouting, fromFlatRouting, toFlatRouting } from '../components/RoutingEditor'
import { flagInfo } from '../components/questionnaireFlags'

// Roles allowed to write (matches api/auth.py require_role(OWNER, ANALYST)).
const WRITE_ROLES = new Set(['owner', 'analyst'])

// --- Small presentational helpers -------------------------------------------

function lowestConfidence(conf: Record<string, number>): number | null {
  const vals = Object.values(conf)
  if (vals.length === 0) return null
  return Math.min(...vals)
}

function ConfidenceDot({ conf }: { conf: Record<string, number> }) {
  const low = lowestConfidence(conf)
  if (low == null) {
    return <span className="inline-block w-2.5 h-2.5 rounded-full bg-slate-300" title="No confidence data" />
  }
  const color = low >= 0.85 ? 'bg-emerald-500' : low >= 0.6 ? 'bg-amber-400' : 'bg-red-500'
  return (
    <span
      className={`inline-block w-2.5 h-2.5 rounded-full ${color}`}
      title={`Lowest field confidence: ${(low * 100).toFixed(0)}%`}
    />
  )
}

function FlagBadge({ code }: { code: string }) {
  const info = flagInfo(code)
  const classes = info.severity === 'error'
    ? 'bg-red-50 text-red-700 ring-red-600/20'
    : 'bg-amber-50 text-amber-700 ring-amber-600/20'
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ring-1 ring-inset ${classes}`}
      title={info.description}
    >
      {info.label}
    </span>
  )
}

// --- Upload panel -----------------------------------------------------------

function UploadPanel({ studyId, onUploaded, disabled }: { studyId: string; onUploaded: () => void; disabled: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const handleFile = async (file: File) => {
    setError(null)
    setUploading(true)
    setProgress(0)
    try {
      await questionnaireApi.upload(studyId, file, setProgress)
      onUploaded()
    } catch (err) {
      setError(errorMessage(err) || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-6">
      <h2 className="font-semibold text-slate-900 mb-1">Upload questionnaire</h2>
      <p className="text-sm text-slate-500 mb-4">
        Upload a .docx or .pdf questionnaire. It will be extracted into structured questions for review.
      </p>
      <input
        ref={inputRef}
        type="file"
        accept=".docx,.pdf"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) handleFile(f)
          e.target.value = ''
        }}
      />
      <button
        onClick={() => inputRef.current?.click()}
        disabled={uploading || disabled}
        className="inline-flex items-center gap-2 bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600 disabled:opacity-50"
      >
        {uploading ? `Uploading… ${progress}%` : 'Choose file'}
      </button>
      {disabled && (
        <p className="text-xs text-slate-400 mt-2">You have read-only access; uploading is disabled.</p>
      )}
      {error && <p className="text-sm text-red-600 mt-3">{error}</p>}
    </div>
  )
}

// --- Question list ----------------------------------------------------------

function QuestionRow({
  q,
  selected,
  onClick,
}: {
  q: Question
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-slate-100 hover:bg-slate-50 transition-colors ${
        selected ? 'bg-sky-50' : ''
      }`}
    >
      <div className="flex items-center gap-2">
        <ConfidenceDot conf={q.extraction_confidence} />
        <span className="font-mono text-xs text-slate-700 shrink-0">{q.qid}</span>
        <span className="text-xs text-slate-400 shrink-0">{q.type}</span>
        {q.approved && (
          <span className="text-[11px] text-emerald-600 shrink-0" title="Approved">
            ✓ approved
          </span>
        )}
      </div>
      <p className="text-sm text-slate-800 mt-1 truncate">{q.text || <em className="text-slate-400">(no text)</em>}</p>
      {q.flags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {q.flags.map((f) => (
            <FlagBadge key={f} code={f} />
          ))}
        </div>
      )}
    </button>
  )
}

// --- Question detail / correction -------------------------------------------

interface DetailState {
  text: string
  type: string
  options: QuestionOption[]
  showIf: FlatRouting
  terminateIf: FlatRouting
  showIfVerbatim: string | null
  terminateIfVerbatim: string | null
}

function buildDetailState(q: Question): DetailState {
  return {
    text: q.text,
    type: q.type,
    options: q.options.map((o) => ({ ...o })),
    showIf: toFlatRouting(q.show_if),
    terminateIf: toFlatRouting(q.terminate_if),
    showIfVerbatim: q.show_if?.verbatim_text ?? null,
    terminateIfVerbatim: q.terminate_if?.verbatim_text ?? null,
  }
}

function QuestionDetail({
  studyId,
  q,
  priorQids,
  canWrite,
  onSaved,
  onApproved,
}: {
  studyId: string
  q: Question
  priorQids: string[]
  canWrite: boolean
  onSaved: (updated: Question) => void
  onApproved: (qid: string) => void
}) {
  const [state, setState] = useState<DetailState>(() => buildDetailState(q))
  const [saving, setSaving] = useState(false)
  const [approving, setApproving] = useState(false)
  const [problems, setProblems] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [savedOk, setSavedOk] = useState(false)

  // Reset local form when the selected question changes.
  useEffect(() => {
    setState(buildDetailState(q))
    setProblems([])
    setError(null)
    setSavedOk(false)
  }, [q])

  const set = <K extends keyof DetailState>(key: K, value: DetailState[K]) => {
    setState((s) => ({ ...s, [key]: value }))
    setSavedOk(false)
  }

  const hasOptions = ['single', 'multi', 'dropdown', 'rank'].includes(state.type)

  const setOption = (i: number, patch: Partial<QuestionOption>) =>
    set('options', state.options.map((o, idx) => (idx === i ? { ...o, ...patch } : o)))
  const addOption = () => {
    const nextCode = state.options.reduce((m, o) => Math.max(m, o.code), 0) + 1
    set('options', [...state.options, { code: nextCode, label: '' }])
  }
  const removeOption = (i: number) => set('options', state.options.filter((_, idx) => idx !== i))

  const handleSave = async () => {
    setSaving(true)
    setProblems([])
    setError(null)
    setSavedOk(false)
    try {
      const showIf = fromFlatRouting(state.showIf, state.showIfVerbatim)
      const terminateIf = fromFlatRouting(state.terminateIf, state.terminateIfVerbatim)
      const patch = {
        text: state.text,
        type: state.type,
        options: hasOptions
          ? state.options.map((o) => ({ code: o.code, label: o.label }))
          : [],
        show_if: (showIf as unknown as Record<string, unknown>) ?? null,
        terminate_if: (terminateIf as unknown as Record<string, unknown>) ?? null,
      }
      const res = await questionnaireApi.update(studyId, q.qid, patch)
      onSaved(res.data)
      setSavedOk(true)
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 422) {
        const detail = err.response.data?.detail
        const probs = detail?.problems ?? detail
        setProblems(Array.isArray(probs) ? probs : [String(probs)])
      } else {
        setError(errorMessage(err) || 'Save failed')
      }
    } finally {
      setSaving(false)
    }
  }

  const handleApprove = async () => {
    setApproving(true)
    setError(null)
    try {
      await questionnaireApi.approve(studyId, q.qid)
      onApproved(q.qid)
    } catch (err) {
      setError(errorMessage(err) || 'Approve failed')
    } finally {
      setApproving(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="font-semibold text-slate-900">
            <span className="font-mono">{q.qid}</span>
          </h2>
          {q.approved && <span className="text-xs text-emerald-600">✓ approved</span>}
        </div>
      </div>

      {q.flags.length > 0 && (
        <div className="space-y-1.5">
          {q.flags.map((f) => {
            const info = flagInfo(f)
            return (
              <div
                key={f}
                className={`text-xs rounded-md px-3 py-2 ${
                  info.severity === 'error' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-700'
                }`}
              >
                <span className="font-medium">{info.label}</span>
                {info.blocking && <span className="ml-1 text-[10px] uppercase">(blocks approval)</span>}
                <span className="block mt-0.5 opacity-90">{info.description}</span>
              </div>
            )
          })}
        </div>
      )}

      {/* Source excerpt — reviewers check extraction against origin */}
      {q.source_text && (
        <div>
          <span className="text-xs font-medium uppercase tracking-wide text-slate-500">Source text</span>
          {q.source_location && <span className="text-xs text-slate-400 ml-2">{q.source_location}</span>}
          <p className="text-sm text-slate-600 bg-slate-50 border border-slate-200 rounded-md p-3 mt-1 whitespace-pre-wrap">
            {q.source_text}
          </p>
        </div>
      )}

      {/* Text */}
      <div>
        <label className="text-xs font-medium uppercase tracking-wide text-slate-500 block mb-1">
          Question text
        </label>
        <textarea
          value={state.text}
          disabled={!canWrite}
          onChange={(e) => set('text', e.target.value)}
          rows={2}
          className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:bg-slate-50"
        />
      </div>

      {/* Type */}
      <div>
        <label className="text-xs font-medium uppercase tracking-wide text-slate-500 block mb-1">Type</label>
        <select
          value={state.type}
          disabled={!canWrite}
          onChange={(e) => set('type', e.target.value)}
          className="h-9 px-3 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:bg-slate-50"
        >
          {QUESTION_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>

      {/* Options */}
      {hasOptions && (
        <div>
          <span className="text-xs font-medium uppercase tracking-wide text-slate-500 block mb-2">Options</span>
          <div className="space-y-2">
            {state.options.map((o, i) => (
              <div key={i} className="flex items-center gap-2">
                <input
                  type="number"
                  value={o.code}
                  disabled={!canWrite}
                  onChange={(e) => setOption(i, { code: Number(e.target.value) })}
                  className="h-8 w-20 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
                  aria-label="option code"
                />
                <input
                  value={o.label}
                  disabled={!canWrite}
                  onChange={(e) => setOption(i, { label: e.target.value })}
                  placeholder="label"
                  className="h-8 flex-1 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
                  aria-label="option label"
                />
                {canWrite && (
                  <button
                    type="button"
                    onClick={() => removeOption(i)}
                    className="text-slate-400 hover:text-red-600 px-1"
                    aria-label="remove option"
                  >
                    ✕
                  </button>
                )}
              </div>
            ))}
            {state.options.length === 0 && (
              <p className="text-sm text-slate-400">No options.</p>
            )}
          </div>
          {canWrite && (
            <button
              type="button"
              onClick={addOption}
              className="mt-2 text-xs text-primary-500 hover:text-primary-600 font-medium"
            >
              + Add option
            </button>
          )}
        </div>
      )}

      {/* Routing: show_if & terminate_if, each beside its verbatim source */}
      <RoutingBlock
        label="Show this question if"
        flat={state.showIf}
        verbatim={state.showIfVerbatim}
        priorQids={priorQids}
        canWrite={canWrite}
        onChangeFlat={(f) => set('showIf', f)}
      />
      <RoutingBlock
        label="Terminate / screen out if"
        flat={state.terminateIf}
        verbatim={state.terminateIfVerbatim}
        priorQids={priorQids}
        canWrite={canWrite}
        onChangeFlat={(f) => set('terminateIf', f)}
      />

      {/* Validation problems from a rejected save */}
      {problems.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-md p-3">
          <p className="text-sm font-medium text-red-700 mb-1">This edit was rejected as invalid:</p>
          <ul className="list-disc list-inside text-sm text-red-600 space-y-0.5">
            {problems.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
      {savedOk && <p className="text-sm text-emerald-600">Saved.</p>}

      {/* Actions */}
      {canWrite && (
        <div className="flex items-center gap-3 pt-2 border-t border-slate-100">
          <button
            onClick={handleSave}
            disabled={saving}
            className="bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600 disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save changes'}
          </button>
          <button
            onClick={handleApprove}
            disabled={approving || q.approved}
            className="bg-emerald-600 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-emerald-700 disabled:opacity-50"
          >
            {q.approved ? 'Approved' : approving ? 'Approving…' : 'Approve'}
          </button>
        </div>
      )}
    </div>
  )
}

function RoutingBlock({
  label,
  flat,
  verbatim,
  priorQids,
  canWrite,
  onChangeFlat,
}: {
  label: string
  flat: FlatRouting
  verbatim: string | null
  priorQids: string[]
  canWrite: boolean
  onChangeFlat: (f: FlatRouting) => void
}) {
  // Only render terminate/show blocks when there is something to show or the
  // reviewer can add one.
  if (!canWrite && flat.conditions.length === 0 && !flat.raw && !verbatim) return null
  return (
    <div className="grid grid-cols-2 gap-4 border border-slate-200 rounded-md p-3">
      <RoutingEditor label={label} flat={flat} onChange={onChangeFlat} qidOptions={priorQids} disabled={!canWrite} />
      <div>
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500 block mb-1">
          Verbatim source
        </span>
        <p className="text-sm text-slate-600 bg-slate-50 border border-slate-200 rounded-md p-2 whitespace-pre-wrap min-h-[2.5rem]">
          {verbatim || <em className="text-slate-400">No routing text in source.</em>}
        </p>
      </div>
    </div>
  )
}

// --- Page -------------------------------------------------------------------

export default function QuestionnairePage() {
  const { id: studyId } = useParams<{ id: string }>()
  const { user } = useAuth()
  const canWrite = !!user && WRITE_ROLES.has(user.role)

  const [status, setStatus] = useState<QuestionnaireStatus | null>(null)
  const [questions, setQuestions] = useState<Question[]>([])
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [selectedQid, setSelectedQid] = useState<string | null>(null)
  const [approveAllError, setApproveAllError] = useState<UnresolvedQuestion[] | null>(null)
  const [approvingAll, setApprovingAll] = useState(false)
  const [approvedOk, setApprovedOk] = useState(false)

  const loadStatus = useCallback(async () => {
    if (!studyId) return
    try {
      const res = await questionnaireApi.status(studyId)
      setStatus(res.data)
      setNotFound(false)
      return res.data
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        setNotFound(true)
        setStatus(null)
      }
    } finally {
      setLoading(false)
    }
  }, [studyId])

  const loadQuestions = useCallback(async () => {
    if (!studyId) return
    try {
      const res = await questionnaireApi.questions(studyId)
      setQuestions(res.data.questions)
    } catch {
      /* still processing / none — status drives the UI */
    }
  }, [studyId])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  // Poll while processing (mirrors ProjectPage's setInterval-until-terminal
  // pattern). Stop as soon as status leaves "processing"; then load questions.
  useEffect(() => {
    if (!status) return
    if (status.status === 'processing') {
      const t = setInterval(loadStatus, 2500)
      return () => clearInterval(t)
    }
    if (status.status !== 'error') {
      loadQuestions()
    }
  }, [status?.status, loadStatus, loadQuestions])

  const priorQidsFor = useCallback(
    (qid: string) => {
      const idx = questions.findIndex((q) => q.qid === qid)
      return questions.slice(0, idx < 0 ? questions.length : idx + 1).map((q) => q.qid)
    },
    [questions],
  )

  const selected = useMemo(
    () => questions.find((q) => q.qid === selectedQid) ?? null,
    [questions, selectedQid],
  )

  const onQuestionSaved = (updated: Question) => {
    setQuestions((qs) => qs.map((q) => (q.qid === updated.qid ? updated : q)))
  }
  const onQuestionApproved = (qid: string) => {
    setQuestions((qs) => qs.map((q) => (q.qid === qid ? { ...q, approved: true } : q)))
    setApproveAllError(null)
  }

  const handleApproveAll = async () => {
    if (!studyId) return
    setApprovingAll(true)
    setApproveAllError(null)
    setApprovedOk(false)
    try {
      await questionnaireApi.approveAll(studyId)
      setApprovedOk(true)
      loadStatus()
      loadQuestions()
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 422) {
        setApproveAllError(err.response.data?.detail?.unresolved ?? [])
      } else {
        setApproveAllError([{ qid: '—', reasons: [errorMessage(err) || 'Approve-all failed'] }])
      }
    } finally {
      setApprovingAll(false)
    }
  }

  if (loading) return <LoadingSpinner />

  const nApproved = questions.filter((q) => q.approved).length

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link to={`/projects/${studyId}`} className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to project
        </Link>
        <h1 className="text-2xl font-semibold text-slate-900 mt-1">Questionnaire review</h1>
        {status && (
          <p className="text-sm text-slate-500 mt-1">
            {status.title ?? status.source_filename} · status: <span className="font-medium">{status.status}</span>
            {status.n_questions > 0 && ` · ${nApproved}/${status.n_questions} approved`}
          </p>
        )}
      </div>

      {/* No questionnaire yet OR error → upload */}
      {(notFound || status?.status === 'error') && (
        <div className="space-y-4 max-w-xl">
          {status?.status === 'error' && (
            <p className="text-sm text-red-600 bg-red-50 rounded-md p-3">
              Extraction failed for the last upload. You can upload again.
            </p>
          )}
          <UploadPanel studyId={studyId!} onUploaded={() => { setLoading(true); loadStatus() }} disabled={!canWrite} />
        </div>
      )}

      {/* Processing */}
      {status?.status === 'processing' && (
        <div className="bg-white rounded-lg border border-slate-200 p-8 text-center">
          <LoadingSpinner />
          <p className="text-sm text-slate-500 mt-3">Extracting questions from the document…</p>
        </div>
      )}

      {/* Ready → list + detail */}
      {status && !notFound && !['processing', 'error'].includes(status.status) && (
        <>
          {status.validation_errors.length > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-md p-3 mb-4">
              <p className="text-sm font-medium text-amber-800 mb-1">Outstanding validation issues:</p>
              <ul className="list-disc list-inside text-sm text-amber-700">
                {status.validation_errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          )}

          {approveAllError && (
            <div className="bg-red-50 border border-red-200 rounded-md p-3 mb-4">
              <p className="text-sm font-medium text-red-700 mb-1">
                Cannot approve the questionnaire — these questions are unresolved:
              </p>
              <ul className="text-sm text-red-600 space-y-0.5">
                {approveAllError.map((u) => (
                  <li key={u.qid}>
                    <button
                      className="font-mono underline hover:text-red-800"
                      onClick={() => setSelectedQid(u.qid)}
                    >
                      {u.qid}
                    </button>
                    {': '}
                    {u.reasons.join('; ')}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {approvedOk && (
            <div className="bg-emerald-50 border border-emerald-200 rounded-md p-3 mb-4 text-sm text-emerald-700">
              Questionnaire approved.
            </div>
          )}

          <div className="grid grid-cols-5 gap-6">
            {/* List */}
            <div className="col-span-2 bg-white rounded-lg border border-slate-200 overflow-hidden h-fit">
              <div className="px-3 py-2 border-b border-slate-200 flex items-center justify-between">
                <span className="text-sm font-medium text-slate-700">{questions.length} questions</span>
                {canWrite && (
                  <button
                    onClick={handleApproveAll}
                    disabled={approvingAll || questions.length === 0}
                    className="text-xs bg-emerald-600 text-white font-medium px-3 py-1.5 rounded-md hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {approvingAll ? 'Approving…' : 'Approve all'}
                  </button>
                )}
              </div>
              <div className="max-h-[70vh] overflow-y-auto">
                {questions.map((q) => (
                  <QuestionRow
                    key={q.qid}
                    q={q}
                    selected={q.qid === selectedQid}
                    onClick={() => setSelectedQid(q.qid)}
                  />
                ))}
                {questions.length === 0 && (
                  <p className="text-sm text-slate-400 p-4">No questions extracted.</p>
                )}
              </div>
            </div>

            {/* Detail */}
            <div className="col-span-3 bg-white rounded-lg border border-slate-200 p-6">
              {selected ? (
                <QuestionDetail
                  key={selected.qid}
                  studyId={studyId!}
                  q={selected}
                  priorQids={priorQidsFor(selected.qid).filter((qid) => qid !== selected.qid)}
                  canWrite={canWrite}
                  onSaved={onQuestionSaved}
                  onApproved={onQuestionApproved}
                />
              ) : (
                <p className="text-sm text-slate-400">Select a question to review its details.</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// Question objects carry study_id only when we attach it in this page; declared
// via intersection above. Helper to pull an error message from an axios error.
function errorMessage(err: unknown): string | null {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail?.error) return detail.error
    return err.message
  }
  return err instanceof Error ? err.message : null
}
