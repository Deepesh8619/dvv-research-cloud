import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import axios from 'axios'
import {
  Question,
  QuestionnaireStatus,
  QUESTION_TYPES,
  QuestionOption,
  questionnaireApi,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import LoadingSpinner from '../components/LoadingSpinner'
import RoutingEditor, { FlatRouting, fromFlatRouting, toFlatRouting } from '../components/RoutingEditor'

// Roles allowed to write (matches api/auth.py require_role(OWNER, ANALYST)).
const WRITE_ROLES = new Set(['owner', 'analyst'])

const OPTION_TYPES = new Set(['single', 'multi', 'dropdown', 'rank'])

function errorMessage(err: unknown): string | null {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail?.error) return detail.error
    return err.message
  }
  return err instanceof Error ? err.message : null
}

/** Next free Q-number id from existing qids matching /^Q(\d+)$/. */
function suggestNextQid(questions: Question[]): string {
  const max = questions.reduce((m, q) => {
    const match = /^Q(\d+)$/.exec(q.qid)
    return match ? Math.max(m, Number(match[1])) : m
  }, 0)
  return `Q${max + 1}`
}

// --- Reusable code+label list editor (options / grid rows / grid cols) --------

function OptionListEditor({
  label,
  options,
  canWrite,
  onChange,
}: {
  label: string
  options: QuestionOption[]
  canWrite: boolean
  onChange: (opts: QuestionOption[]) => void
}) {
  const setOne = (i: number, patch: Partial<QuestionOption>) =>
    onChange(options.map((o, idx) => (idx === i ? { ...o, ...patch } : o)))
  const add = () => {
    const nextCode = options.reduce((m, o) => Math.max(m, o.code), 0) + 1
    onChange([...options, { code: nextCode, label: '' }])
  }
  const remove = (i: number) => onChange(options.filter((_, idx) => idx !== i))

  return (
    <div>
      <span className="text-xs font-medium uppercase tracking-wide text-slate-500 block mb-2">{label}</span>
      <div className="space-y-2">
        {options.map((o, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              type="number"
              value={o.code}
              disabled={!canWrite}
              onChange={(e) => setOne(i, { code: Number(e.target.value) })}
              className="h-8 w-20 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
              aria-label={`${label} code`}
            />
            <input
              value={o.label}
              disabled={!canWrite}
              onChange={(e) => setOne(i, { label: e.target.value })}
              placeholder="label"
              className="h-8 flex-1 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
              aria-label={`${label} label`}
            />
            {canWrite && (
              <button
                type="button"
                onClick={() => remove(i)}
                className="text-slate-400 hover:text-red-600 px-1"
                aria-label={`remove ${label}`}
              >
                ✕
              </button>
            )}
          </div>
        ))}
        {options.length === 0 && <p className="text-sm text-slate-400">None.</p>}
      </div>
      {canWrite && (
        <button
          type="button"
          onClick={add}
          className="mt-2 text-xs text-primary-500 hover:text-primary-600 font-medium"
        >
          + Add {label.toLowerCase()}
        </button>
      )}
    </div>
  )
}

// --- Detail editor ------------------------------------------------------------

interface DetailState {
  qid: string
  text: string
  type: string
  required: boolean
  options: QuestionOption[]
  rows: QuestionOption[]
  cols: QuestionOption[]
  showIf: FlatRouting
  terminateIf: FlatRouting
}

function buildDetailState(q: Question): DetailState {
  return {
    qid: q.qid,
    text: q.text,
    type: q.type,
    required: q.required,
    options: (q.options ?? []).map((o) => ({ ...o })),
    rows: (q.rows ?? []).map((o) => ({ ...o })),
    cols: (q.cols ?? []).map((o) => ({ ...o })),
    showIf: toFlatRouting(q.show_if),
    terminateIf: toFlatRouting(q.terminate_if),
  }
}

function QuestionDetail({
  studyId,
  q,
  priorQids,
  canWrite,
  onSaved,
}: {
  studyId: string
  q: Question
  priorQids: string[]
  canWrite: boolean
  onSaved: (oldQid: string, updated: Question) => void
}) {
  const [state, setState] = useState<DetailState>(() => buildDetailState(q))
  const [saving, setSaving] = useState(false)
  const [problems, setProblems] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [savedOk, setSavedOk] = useState(false)

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

  const hasOptions = OPTION_TYPES.has(state.type)
  const isGrid = state.type === 'grid_single'

  const handleSave = async () => {
    setSaving(true)
    setProblems([])
    setError(null)
    setSavedOk(false)
    try {
      const showIf = fromFlatRouting(state.showIf, null)
      const terminateIf = fromFlatRouting(state.terminateIf, null)
      const patch = {
        qid: state.qid.trim() || q.qid,
        text: state.text,
        type: state.type,
        required: state.required,
        options: hasOptions ? state.options.map((o) => ({ code: o.code, label: o.label })) : [],
        rows: isGrid ? state.rows.map((o) => ({ code: o.code, label: o.label })) : null,
        cols: isGrid ? state.cols.map((o) => ({ code: o.code, label: o.label })) : null,
        show_if: (showIf as unknown as Record<string, unknown>) ?? null,
        terminate_if: (terminateIf as unknown as Record<string, unknown>) ?? null,
      }
      const res = await questionnaireApi.update(studyId, q.qid, patch)
      onSaved(q.qid, res.data)
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

  return (
    <div className="space-y-5">
      {/* qid */}
      <div>
        <label className="text-xs font-medium uppercase tracking-wide text-slate-500 block mb-1">
          Question ID
        </label>
        <input
          value={state.qid}
          disabled={!canWrite}
          onChange={(e) => set('qid', e.target.value)}
          className="h-9 w-40 px-3 rounded-md border border-slate-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:bg-slate-50"
        />
        <p className="text-xs text-slate-400 mt-1">
          Renaming rewrites every reference to this question consistently.
        </p>
      </div>

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

      {/* Type + required */}
      <div className="flex items-end gap-6">
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
        <label className="flex items-center gap-2 text-sm text-slate-700 h-9">
          <input
            type="checkbox"
            checked={state.required}
            disabled={!canWrite}
            onChange={(e) => set('required', e.target.checked)}
            className="h-4 w-4"
          />
          Required
        </label>
      </div>

      {/* Options */}
      {hasOptions && (
        <OptionListEditor label="Options" options={state.options} canWrite={canWrite} onChange={(o) => set('options', o)} />
      )}

      {/* Grid rows/cols */}
      {isGrid && (
        <div className="grid grid-cols-2 gap-4">
          <OptionListEditor label="Rows" options={state.rows} canWrite={canWrite} onChange={(o) => set('rows', o)} />
          <OptionListEditor label="Cols" options={state.cols} canWrite={canWrite} onChange={(o) => set('cols', o)} />
        </div>
      )}

      {/* Routing — show_if references prior questions only (a question can't
          gate its own display, and the backend rejects forward references).
          terminate_if additionally offers the question ITSELF: "terminate if
          S1 = 3" on S1 is the canonical screener pattern, explicitly allowed
          by SurveyDefinition.validate(). */}
      <div className="border border-slate-200 rounded-md p-3">
        <RoutingEditor
          label="Show this question if"
          flat={state.showIf}
          onChange={(f) => set('showIf', f)}
          qidOptions={priorQids}
          disabled={!canWrite}
        />
      </div>
      <div className="border border-slate-200 rounded-md p-3">
        <RoutingEditor
          label="Terminate / screen out if"
          flat={state.terminateIf}
          onChange={(f) => set('terminateIf', f)}
          qidOptions={[...priorQids, q.qid]}
          disabled={!canWrite}
        />
      </div>

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

      {canWrite && (
        <div className="pt-2 border-t border-slate-100">
          <button
            onClick={handleSave}
            disabled={saving}
            className="bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600 disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      )}
    </div>
  )
}

// --- Add-question form --------------------------------------------------------

function AddQuestionForm({
  suggestedQid,
  onAdd,
  onCancel,
}: {
  suggestedQid: string
  onAdd: (qid: string, type: string) => Promise<string | null>
  onCancel: () => void
}) {
  const [qid, setQid] = useState(suggestedQid)
  const [type, setType] = useState<string>('single')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    const err = await onAdd(qid.trim(), type)
    if (err) {
      setError(err)
      setBusy(false)
    }
    // On success the parent unmounts this form.
  }

  return (
    <div className="p-3 border-b border-slate-200 bg-slate-50 space-y-2">
      <div className="flex items-center gap-2">
        <input
          value={qid}
          onChange={(e) => setQid(e.target.value)}
          placeholder="qid"
          className="h-8 w-24 px-2 rounded-md border border-slate-300 text-sm font-mono"
          aria-label="new question id"
        />
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="h-8 px-2 rounded-md border border-slate-300 text-sm flex-1"
          aria-label="new question type"
        >
          {QUESTION_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="flex items-center gap-2">
        <button
          onClick={submit}
          disabled={busy || !qid.trim()}
          className="text-xs bg-primary-500 text-white font-medium px-3 py-1.5 rounded-md hover:bg-primary-600 disabled:opacity-50"
        >
          {busy ? 'Adding…' : 'Add'}
        </button>
        <button onClick={onCancel} className="text-xs text-slate-500 hover:text-slate-700 px-2">
          Cancel
        </button>
      </div>
    </div>
  )
}

// --- Create (title prompt) ----------------------------------------------------

function CreatePanel({ onCreate, canWrite }: { onCreate: (title: string) => Promise<string | null>; canWrite: boolean }) {
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    const err = await onCreate(title.trim() || 'Untitled survey')
    if (err) {
      setError(err)
      setBusy(false)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-slate-200 p-6 max-w-xl">
      <h2 className="font-semibold text-slate-900 mb-1">Build a survey from scratch</h2>
      <p className="text-sm text-slate-500 mb-4">
        Give your survey a title, then add questions one by one.
      </p>
      {!canWrite ? (
        <p className="text-sm text-slate-400">You have read-only access; building is disabled.</p>
      ) : (
        <>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Survey title"
            className="w-full h-9 px-3 rounded-md border border-slate-300 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-sky-500"
          />
          {error && <p className="text-sm text-red-600 mb-3">{error}</p>}
          <button
            onClick={submit}
            disabled={busy}
            className="bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600 disabled:opacity-50"
          >
            {busy ? 'Creating…' : 'Create survey'}
          </button>
        </>
      )}
    </div>
  )
}

// --- Page ---------------------------------------------------------------------

export default function SurveyBuilderPage() {
  const { id: studyId } = useParams<{ id: string }>()
  const { user } = useAuth()
  const canWrite = !!user && WRITE_ROLES.has(user.role)

  const [status, setStatus] = useState<QuestionnaireStatus | null>(null)
  const [questions, setQuestions] = useState<Question[]>([])
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedQid, setSelectedQid] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [rowError, setRowError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!studyId) return
    setLoadError(null)
    try {
      const res = await questionnaireApi.status(studyId)
      setStatus(res.data)
      setNotFound(false)
      if (res.data.source !== 'extracted' && !['processing', 'error'].includes(res.data.status)) {
        try {
          const qs = await questionnaireApi.questions(studyId)
          setQuestions(qs.data.questions)
        } catch (err) {
          setLoadError(errorMessage(err) || 'Could not load questions. Please try again.')
        }
      }
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        setNotFound(true)
        setStatus(null)
      } else {
        setStatus(null)
        setLoadError(errorMessage(err) || 'Could not load the survey. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }, [studyId])

  useEffect(() => {
    load()
  }, [load])

  const handleCreate = async (title: string): Promise<string | null> => {
    if (!studyId) return 'No study'
    try {
      await questionnaireApi.createAuthored(studyId, title)
      await load()
      return null
    } catch (err) {
      return errorMessage(err) || 'Could not create the survey.'
    }
  }

  const handleAdd = async (qid: string, type: string): Promise<string | null> => {
    if (!studyId) return 'No study'
    try {
      const res = await questionnaireApi.addQuestion(studyId, { qid, type })
      setQuestions((qs) => [...qs, res.data])
      setSelectedQid(res.data.qid)
      setAdding(false)
      return null
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 422) {
        const probs = err.response.data?.detail?.problems
        if (Array.isArray(probs)) return probs.join('; ')
      }
      return errorMessage(err) || 'Could not add the question.'
    }
  }

  const handleDelete = async (qid: string) => {
    if (!studyId) return
    if (!window.confirm(`Delete ${qid}? This cannot be undone.`)) return
    setRowError(null)
    try {
      await questionnaireApi.deleteQuestion(studyId, qid)
      setQuestions((qs) => qs.filter((q) => q.qid !== qid))
      if (selectedQid === qid) setSelectedQid(null)
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 422) {
        const refs = err.response.data?.detail?.referenced_by
        setRowError(
          `Cannot delete ${qid} — its routing is referenced by ${
            Array.isArray(refs) ? refs.join(', ') : 'another question'
          }. Fix those first.`,
        )
        return
      }
      setRowError(errorMessage(err) || 'Could not delete the question.')
    }
  }

  const handleMove = async (index: number, dir: -1 | 1) => {
    if (!studyId) return
    const target = index + dir
    if (target < 0 || target >= questions.length) return
    const reordered = [...questions]
    ;[reordered[index], reordered[target]] = [reordered[target], reordered[index]]
    setRowError(null)
    // Optimistic; revert on failure.
    const prev = questions
    setQuestions(reordered)
    try {
      await questionnaireApi.reorderQuestions(studyId, reordered.map((q) => q.qid))
    } catch (err) {
      setQuestions(prev)
      if (axios.isAxiosError(err) && err.response?.status === 422) {
        const probs = err.response.data?.detail?.problems
        setRowError(
          Array.isArray(probs)
            ? `This order is invalid: ${probs.join('; ')}`
            : 'This order would break routing (a question moved ahead of one it references).',
        )
        return
      }
      setRowError(errorMessage(err) || 'Could not reorder.')
    }
  }

  const onQuestionSaved = (oldQid: string, updated: Question) => {
    setQuestions((qs) => qs.map((q) => (q.qid === oldQid ? updated : q)))
    if (selectedQid === oldQid) setSelectedQid(updated.qid)
  }

  const selected = useMemo(
    () => questions.find((q) => q.qid === selectedQid) ?? null,
    [questions, selectedQid],
  )

  const priorQids = useMemo(() => {
    if (!selected) return []
    const idx = questions.findIndex((q) => q.qid === selected.qid)
    return questions.slice(0, idx < 0 ? 0 : idx).map((q) => q.qid)
  }, [questions, selected])

  if (loading) return <LoadingSpinner />

  return (
    <div>
      <div className="mb-6">
        <Link to={`/projects/${studyId}`} className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to project
        </Link>
        <div className="flex items-center justify-between mt-1">
          <h1 className="text-2xl font-semibold text-slate-900">Survey builder</h1>
          {status && status.source !== 'extracted' && (
            <Link
              to={`/projects/${studyId}/questionnaire`}
              className="text-sm font-medium text-primary-500 hover:text-primary-600"
            >
              Ready for review →
            </Link>
          )}
        </div>
        {status && (
          <p className="text-sm text-slate-500 mt-1">
            {status.title ?? status.source_filename} · status: <span className="font-medium">{status.status}</span>
          </p>
        )}
      </div>

      {loadError && (
        <div className="bg-red-50 border border-red-200 rounded-md p-4 max-w-xl">
          <p className="text-sm font-medium text-red-700 mb-1">Couldn&apos;t load the survey</p>
          <p className="text-sm text-red-600">{loadError}</p>
          <button
            onClick={() => { setLoading(true); load() }}
            className="mt-3 text-sm font-medium text-red-700 underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* Extracted questionnaires are owned by the review page — don't edit here. */}
      {!loadError && status?.source === 'extracted' && (
        <div className="bg-amber-50 border border-amber-200 rounded-md p-4 max-w-xl">
          <p className="text-sm text-amber-800 mb-2">
            This questionnaire was extracted from an uploaded document. Edit it on the review page.
          </p>
          <Link
            to={`/projects/${studyId}/questionnaire`}
            className="text-sm font-medium text-primary-600 underline"
          >
            Go to questionnaire review →
          </Link>
        </div>
      )}

      {/* No questionnaire yet → title prompt */}
      {!loadError && notFound && <CreatePanel onCreate={handleCreate} canWrite={canWrite} />}

      {/* Authored draft → builder */}
      {!loadError && status && status.source !== 'extracted' && (
        <div className="grid grid-cols-5 gap-6">
          {/* List */}
          <div className="col-span-2 bg-white rounded-lg border border-slate-200 overflow-hidden h-fit">
            <div className="px-3 py-2 border-b border-slate-200 flex items-center justify-between">
              <span className="text-sm font-medium text-slate-700">{questions.length} questions</span>
              {canWrite && !adding && (
                <button
                  onClick={() => setAdding(true)}
                  className="text-xs bg-primary-500 text-white font-medium px-3 py-1.5 rounded-md hover:bg-primary-600"
                >
                  + Add question
                </button>
              )}
            </div>

            {adding && canWrite && (
              <AddQuestionForm
                suggestedQid={suggestNextQid(questions)}
                onAdd={handleAdd}
                onCancel={() => setAdding(false)}
              />
            )}

            {rowError && (
              <p className="text-xs text-red-600 bg-red-50 border-b border-red-200 px-3 py-2">{rowError}</p>
            )}

            <div className="max-h-[70vh] overflow-y-auto">
              {questions.map((q, i) => (
                <div
                  key={q.qid}
                  className={`flex items-center border-b border-slate-100 ${
                    q.qid === selectedQid ? 'bg-sky-50' : 'hover:bg-slate-50'
                  }`}
                >
                  <button onClick={() => setSelectedQid(q.qid)} className="flex-1 text-left px-3 py-2.5 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-slate-700 shrink-0">{q.qid}</span>
                      <span className="text-xs text-slate-400 shrink-0">{q.type}</span>
                    </div>
                    <p className="text-sm text-slate-800 mt-1 truncate">
                      {q.text || <em className="text-slate-400">(no text)</em>}
                    </p>
                  </button>
                  {canWrite && (
                    <div className="flex items-center gap-0.5 pr-2 shrink-0">
                      <button
                        onClick={() => handleMove(i, -1)}
                        disabled={i === 0}
                        className="text-slate-400 hover:text-slate-700 disabled:opacity-30 px-1"
                        aria-label={`move ${q.qid} up`}
                        title="Move up"
                      >
                        ↑
                      </button>
                      <button
                        onClick={() => handleMove(i, 1)}
                        disabled={i === questions.length - 1}
                        className="text-slate-400 hover:text-slate-700 disabled:opacity-30 px-1"
                        aria-label={`move ${q.qid} down`}
                        title="Move down"
                      >
                        ↓
                      </button>
                      <button
                        onClick={() => handleDelete(q.qid)}
                        className="text-slate-400 hover:text-red-600 px-1"
                        aria-label={`delete ${q.qid}`}
                        title="Delete"
                      >
                        ✕
                      </button>
                    </div>
                  )}
                </div>
              ))}
              {questions.length === 0 && !adding && (
                <p className="text-sm text-slate-400 p-4">
                  No questions yet. {canWrite && 'Click "Add question" to start.'}
                </p>
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
                priorQids={priorQids}
                canWrite={canWrite}
                onSaved={onQuestionSaved}
              />
            ) : (
              <p className="text-sm text-slate-400">Select a question to edit it, or add a new one.</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
