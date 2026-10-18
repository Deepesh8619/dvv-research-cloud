import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import axios from 'axios'
import {
  Question,
  QuestionCreate,
  QuestionMapEntry,
  QuestionnaireStatus,
  QuestionOption,
  questionnaireApi,
} from '../api/client'
import { isConstantSumQuestion, TYPE_CHOICES, typeLabel } from '../labels/questionTypes'
import { useAuth } from '../auth/AuthContext'
import LoadingSpinner from '../components/LoadingSpinner'
import RoutingEditor, { FlatRouting, fromFlatRouting, toFlatRouting } from '../components/RoutingEditor'
import ThemeSelect from '../components/ThemeSelect'
import { DEFAULT_THEME_SLUG } from '../api/themes'
import { canWrite as hasWriteRole } from '../auth/roles'
import { bucketInfo } from '../lib/buckets'
import { ConnectionsPanel } from '../components/review/QuestionMap'
import { AddQuestionPalette } from '../components/builder/AddQuestionPalette'
import { PasteOptionsBox } from '../components/builder/PasteOptionsBox'
import { RespondentPreview } from '../components/builder/RespondentPreview'
import { TypeIcon } from '../components/builder/TypeIcon'
import { defaultQuestionFor } from '../components/builder/typeMeta'

// This is the PROGRAM-FROM-SCRATCH screen: a researcher builds a questionnaire
// one question at a time rather than uploading a document (that path lands on
// QuestionnairePage instead). Same API, same question shape — this only adds
// a faster way to create and see what's being built: a palette instead of a
// bare <select>, paste-a-list instead of one "+ Add option" click per row, and
// a live respondent-eye preview next to the editor instead of imagining it.

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
export function suggestNextQid(questions: Question[]): string {
  const max = questions.reduce((m, q) => {
    const match = /^Q(\d+)$/.exec(q.qid)
    return match ? Math.max(m, Number(match[1])) : m
  }, 0)
  return `Q${max + 1}`
}

/** Insert `created` right after `afterQid` (or at the end when there is none
    to insert after — a fresh survey with nothing selected yet). */
function withInserted(existing: Question[], created: Question, afterQid: string | null): Question[] {
  if (!afterQid) return [...existing, created]
  const idx = existing.findIndex((q) => q.qid === afterQid)
  if (idx < 0) return [...existing, created]
  const next = existing.slice()
  next.splice(idx + 1, 0, created)
  return next
}

// --- Reusable code+label list editor (options / grid rows / grid cols) --------

function OptionListEditor({
  label,
  options,
  canWrite,
  onChange,
  pasteable = false,
}: {
  label: string
  options: QuestionOption[]
  canWrite: boolean
  onChange: (opts: QuestionOption[]) => void
  /** Only a question's own answer options get paste-a-list and the
      exclusive/anchor/other flags — grid rows/cols and constant-sum items are
      plain labels, so this stays off for those. */
  pasteable?: boolean
}) {
  const [dragIndex, setDragIndex] = useState<number | null>(null)

  const setOne = (i: number, patch: Partial<QuestionOption>) =>
    onChange(options.map((o, idx) => (idx === i ? { ...o, ...patch } : o)))
  const add = () => {
    const nextCode = options.reduce((m, o) => Math.max(m, o.code), 0) + 1
    onChange([...options, { code: nextCode, label: '' }])
  }
  const remove = (i: number) => onChange(options.filter((_, idx) => idx !== i))
  const reorder = (from: number, to: number) => {
    if (from === to) return
    const next = options.slice()
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    onChange(next)
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</span>
        {canWrite && pasteable && (
          <PasteOptionsBox onApply={(opts) => onChange([...options, ...opts])} />
        )}
      </div>
      <div className="space-y-2">
        {options.map((o, i) => (
          <div
            key={i}
            draggable={canWrite}
            onDragStart={() => setDragIndex(i)}
            onDragOver={(e) => {
              if (dragIndex === null) return
              e.preventDefault()
            }}
            onDrop={(e) => {
              e.preventDefault()
              if (dragIndex !== null) reorder(dragIndex, i)
              setDragIndex(null)
            }}
            onDragEnd={() => setDragIndex(null)}
            className={`flex flex-wrap items-center gap-2 rounded-md transition-opacity ${
              dragIndex === i ? 'opacity-40' : ''
            } ${canWrite ? 'cursor-grab active:cursor-grabbing' : ''}`}
          >
            <input
              type="number"
              value={o.code}
              disabled={!canWrite}
              onChange={(e) => setOne(i, { code: Number(e.target.value) })}
              className="h-8 w-16 shrink-0 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
              aria-label={`${label} code`}
            />
            <input
              value={o.label}
              disabled={!canWrite}
              onChange={(e) => setOne(i, { label: e.target.value })}
              placeholder="label"
              className="h-8 min-w-[8rem] flex-1 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
              aria-label={`${label} label`}
            />
            {pasteable && (
              <div className="flex shrink-0 items-center gap-2 text-[11px] text-slate-500">
                <label className="flex items-center gap-1" title="Exclusive — picking this clears every other option">
                  <input
                    type="checkbox"
                    checked={!!o.exclusive}
                    disabled={!canWrite}
                    onChange={(e) => setOne(i, { exclusive: e.target.checked })}
                    className="h-3.5 w-3.5"
                    aria-label={`${label} exclusive`}
                  />
                  Excl.
                </label>
                <label className="flex items-center gap-1" title="Anchored — stays in place when options are randomized">
                  <input
                    type="checkbox"
                    checked={!!o.anchored}
                    disabled={!canWrite}
                    onChange={(e) => setOne(i, { anchored: e.target.checked })}
                    className="h-3.5 w-3.5"
                    aria-label={`${label} anchored`}
                  />
                  Anchor
                </label>
                <label className="flex items-center gap-1" title="Write-in — respondent can type their own answer">
                  <input
                    type="checkbox"
                    checked={!!o.open_other}
                    disabled={!canWrite}
                    onChange={(e) => setOne(i, { open_other: e.target.checked })}
                    className="h-3.5 w-3.5"
                    aria-label={`${label} other`}
                  />
                  Other
                </label>
              </div>
            )}
            {canWrite && (
              <button
                type="button"
                onClick={() => remove(i)}
                className="shrink-0 text-slate-400 hover:text-red-600 px-1"
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
  /** Wire type, or the UI-only 'constant_sum' pseudo-type (see
      labels/questionTypes.ts) — resolved back to 'numeric' on save. */
  type: string
  required: boolean
  randomizeOptions: boolean
  options: QuestionOption[]
  rows: QuestionOption[]
  cols: QuestionOption[]
  /** constant_sum only: the total the items must add up to. */
  sumTotal: number
  showIf: FlatRouting
  terminateIf: FlatRouting
}

function buildDetailState(q: Question): DetailState {
  const sum = (q.validation as Record<string, unknown> | null)?.sum
  return {
    qid: q.qid,
    text: q.text,
    type: isConstantSumQuestion(q) ? 'constant_sum' : q.type,
    required: q.required,
    randomizeOptions: q.randomize_options,
    options: (q.options ?? []).map((o) => ({ ...o })),
    rows: (q.rows ?? []).map((o) => ({ ...o })),
    cols: (q.cols ?? []).map((o) => ({ ...o })),
    sumTotal: typeof sum === 'number' ? sum : 100,
    showIf: toFlatRouting(q.show_if),
    terminateIf: toFlatRouting(q.terminate_if),
  }
}

/** The draft as a `Question`-shaped object, for the live preview — built from
    whatever is in the editor right now, not from the last save. */
function draftQuestionFrom(state: DetailState, base: Question): Question {
  const isConstantSum = state.type === 'constant_sum'
  const isGrid = state.type === 'grid_single' || state.type === 'grid_multi'
  const hasOptions = OPTION_TYPES.has(state.type)
  return {
    ...base,
    qid: state.qid.trim() || base.qid,
    text: state.text,
    type: isConstantSum ? 'numeric' : state.type,
    options: hasOptions ? state.options : [],
    rows: isGrid || isConstantSum ? state.rows : null,
    cols: isGrid ? state.cols : null,
    required: state.required,
    randomize_options: state.randomizeOptions,
    validation: isConstantSum ? { min: 0, max: state.sumTotal, sum: state.sumTotal } : base.validation,
  }
}

function QuestionDetail({
  studyId,
  q,
  priorQids,
  earlier,
  entry,
  canWrite,
  autoFocusText,
  onSaved,
  onGoTo,
  onDirtyChange,
  onDraftChange,
  onAutoFocusConsumed,
}: {
  studyId: string
  q: Question
  priorQids: string[]
  earlier: Question[]
  entry: QuestionMapEntry | undefined
  canWrite: boolean
  autoFocusText: boolean
  onSaved: (oldQid: string, updated: Question) => void
  onGoTo: (qid: string) => void
  onDirtyChange: (dirty: boolean) => void
  onDraftChange: (draft: Question) => void
  onAutoFocusConsumed: () => void
}) {
  const [state, setState] = useState<DetailState>(() => buildDetailState(q))
  const textRef = useRef<HTMLTextAreaElement>(null)
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

  useEffect(() => {
    if (autoFocusText) {
      textRef.current?.focus()
      onAutoFocusConsumed()
    }
    // Only on mount — this fires once per selection, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const dirty = useMemo(() => JSON.stringify(state) !== JSON.stringify(buildDetailState(q)), [state, q])
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange])

  const draft = useMemo(() => draftQuestionFrom(state, q), [state, q])
  useEffect(() => onDraftChange(draft), [draft, onDraftChange])

  const set = <K extends keyof DetailState>(key: K, value: DetailState[K]) => {
    setState((s) => ({ ...s, [key]: value }))
    setSavedOk(false)
  }

  const hasOptions = OPTION_TYPES.has(state.type)
  const isGrid = state.type === 'grid_single' || state.type === 'grid_multi'
  const isConstantSum = state.type === 'constant_sum'

  // Insert a piping marker {QID} at the cursor (SurveyJS interpolates it live).
  const insertPiping = (qid: string) => {
    if (!qid) return
    const el = textRef.current
    const marker = `{${qid}}`
    const pos = el ? el.selectionStart : state.text.length
    const next = state.text.slice(0, pos) + marker + state.text.slice(pos)
    set('text', next)
    // Restore focus + caret after the inserted marker.
    requestAnimationFrame(() => {
      if (el) {
        el.focus()
        el.setSelectionRange(pos + marker.length, pos + marker.length)
      }
    })
  }

  const handleSave = async () => {
    setSaving(true)
    setProblems([])
    setError(null)
    setSavedOk(false)
    try {
      const showIf = fromFlatRouting(state.showIf, null)
      const terminateIf = fromFlatRouting(state.terminateIf, null)
      const patch: Record<string, unknown> = {
        qid: state.qid.trim() || q.qid,
        text: state.text,
        // The pseudo-type never reaches the API — what is saved is always the
        // real wire type (numeric), with the rows/total carrying the meaning.
        type: isConstantSum ? 'numeric' : state.type,
        required: state.required,
        randomize_options: hasOptions ? state.randomizeOptions : false,
        options: hasOptions
          ? state.options.map((o) => ({
              code: o.code,
              label: o.label,
              exclusive: !!o.exclusive,
              anchored: !!o.anchored,
              open_other: !!o.open_other,
            }))
          : [],
        rows: isGrid || isConstantSum ? state.rows.map((o) => ({ code: o.code, label: o.label })) : null,
        cols: isGrid ? state.cols.map((o) => ({ code: o.code, label: o.label })) : null,
        show_if: (showIf as unknown as Record<string, unknown>) ?? null,
        terminate_if: (terminateIf as unknown as Record<string, unknown>) ?? null,
      }
      if (isConstantSum) {
        patch.validation = { min: 0, max: state.sumTotal, sum: state.sumTotal }
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
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Question text
          </label>
          {canWrite && priorQids.length > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-slate-500">
              Insert piping:
              <select
                value=""
                onChange={(e) => {
                  insertPiping(e.target.value)
                  e.target.value = ''
                }}
                className="h-7 px-2 rounded border border-slate-300 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500"
                title="Inserts {QID} — shows the respondent's earlier answer, rendered live in the survey."
              >
                <option value="">answer of…</option>
                {priorQids.map((pq) => (
                  <option key={pq} value={pq}>
                    {pq}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <textarea
          ref={textRef}
          value={state.text}
          disabled={!canWrite}
          onChange={(e) => set('text', e.target.value)}
          rows={2}
          className="w-full px-3 py-2 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:bg-slate-50"
        />
        {state.text.includes('{') && (
          <p className="text-xs text-slate-400 mt-1">
            {'{QID}'} markers show the respondent's earlier answer, rendered live in the survey.
          </p>
        )}
      </div>

      {/* Type + required + randomize */}
      <div className="flex items-end gap-6 flex-wrap">
        <div>
          <label className="text-xs font-medium uppercase tracking-wide text-slate-500 block mb-1">Type</label>
          <select
            value={state.type}
            disabled={!canWrite}
            onChange={(e) => set('type', e.target.value)}
            className="h-9 px-3 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:bg-slate-50"
          >
            {TYPE_CHOICES.map((t) => (
              <option key={t} value={t}>
                {typeLabel(t)}
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
        {hasOptions && (
          <label className="flex items-center gap-2 text-sm text-slate-700 h-9">
            <input
              type="checkbox"
              checked={state.randomizeOptions}
              disabled={!canWrite}
              onChange={(e) => set('randomizeOptions', e.target.checked)}
              className="h-4 w-4"
            />
            Randomize options
          </label>
        )}
      </div>

      {/* Options */}
      {hasOptions && (
        <OptionListEditor
          label="Options"
          options={state.options}
          canWrite={canWrite}
          onChange={(o) => set('options', o)}
          pasteable
        />
      )}

      {/* Grid rows/cols */}
      {isGrid && (
        <div className="grid grid-cols-2 gap-4">
          <OptionListEditor label="Rows" options={state.rows} canWrite={canWrite} onChange={(o) => set('rows', o)} />
          <OptionListEditor label="Cols" options={state.cols} canWrite={canWrite} onChange={(o) => set('cols', o)} />
        </div>
      )}

      {/* Constant sum: one box per item, plus the total they must reach */}
      {isConstantSum && (
        <div className="space-y-3">
          <OptionListEditor label="Items" options={state.rows} canWrite={canWrite} onChange={(o) => set('rows', o)} />
          <div>
            <label className="text-xs font-medium uppercase tracking-wide text-slate-500 block mb-1" htmlFor="sum-total">
              Must total
            </label>
            <input
              id="sum-total"
              type="number"
              value={state.sumTotal}
              disabled={!canWrite}
              onChange={(e) => set('sumTotal', Number(e.target.value))}
              className="h-9 w-28 px-3 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
            />
          </div>
        </div>
      )}

      {/* Connections — same panel the review screen uses, so "options from",
          piping and "decides" mean exactly the same thing in both places. */}
      <ConnectionsPanel
        studyId={studyId}
        question={q}
        entry={entry}
        earlier={earlier}
        canWrite={canWrite}
        onChanged={(updated) => onSaved(q.qid, updated)}
        onGoTo={onGoTo}
      />

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
            disabled={saving || !dirty}
            className="bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600 disabled:opacity-50"
          >
            {saving ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
          </button>
        </div>
      )}
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
    <div className="bg-white rounded-lg border border-slate-200 shadow-panel p-6 max-w-xl">
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
  const canWrite = hasWriteRole(user?.role)

  const [status, setStatus] = useState<QuestionnaireStatus | null>(null)
  const [questions, setQuestions] = useState<Question[]>([])
  const [map, setMap] = useState<Record<string, QuestionMapEntry>>({})
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedQid, setSelectedQid] = useState<string | null>(null)
  const [rowError, setRowError] = useState<string | null>(null)
  const [themeMsg, setThemeMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [dirty, setDirty] = useState(false)
  const [draftQuestion, setDraftQuestion] = useState<Question | null>(null)
  const [previewAll, setPreviewAll] = useState(false)

  // Drag-to-reorder state, and its keyboard equivalent (Alt+↑/↓) — mirrors
  // QuestionnairePage's question list exactly, so the two screens behave the
  // same way once a survey has both authored and extracted history.
  const [dragQid, setDragQid] = useState<string | null>(null)
  const [dropBeforeQid, setDropBeforeQid] = useState<string | null>(null)
  // Set right after a palette insert so the new question's text field gets
  // focus once, without refocusing every time the same question is reselected.
  const autoFocusRef = useRef<string | null>(null)

  const handleThemeChange = async (slug: string) => {
    if (!studyId) return
    setThemeMsg(null)
    try {
      const res = await questionnaireApi.settings(studyId, slug)
      setStatus(res.data)
      setThemeMsg({ ok: true, text: 'Theme saved.' })
    } catch (err) {
      setThemeMsg({ ok: false, text: errorMessage(err) || 'Could not save theme.' })
    }
  }

  const loadMap = useCallback(async () => {
    if (!studyId) return
    try {
      const res = await questionnaireApi.map(studyId)
      setMap(res.data.questions)
    } catch {
      // The bucket stripe/connections just show less — not worth a visible error.
    }
  }, [studyId])

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
          void loadMap()
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
  }, [studyId, loadMap])

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

  /** Shared by the palette and "Duplicate": create the question, patch in a
      pseudo-type's validation if it needs one, then land it right after
      whatever was selected instead of at the end of the list. */
  const createAndPlace = async (
    qid: string,
    create: Omit<QuestionCreate, 'qid'>,
    validation: Record<string, unknown> | undefined,
    afterQid: string | null,
  ): Promise<string | null> => {
    if (!studyId) return 'No study'
    try {
      const res = await questionnaireApi.addQuestion(studyId, { ...create, qid })
      let created = res.data
      if (validation) {
        created = (await questionnaireApi.update(studyId, qid, { validation })).data
      }
      const ordered = withInserted(questions, created, afterQid)
      setQuestions(ordered)
      setSelectedQid(created.qid)
      if (afterQid) {
        try {
          await questionnaireApi.reorderQuestions(studyId, ordered.map((q) => q.qid))
        } catch {
          // The question exists, just at the end — the next reorder fixes it.
        }
      }
      void loadMap()
      return null
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 422) {
        const probs = err.response.data?.detail?.problems
        if (Array.isArray(probs)) return probs.join('; ')
      }
      return errorMessage(err) || 'Could not add the question.'
    }
  }

  const handleAddFromPalette = async (key: string) => {
    setRowError(null)
    const spec = defaultQuestionFor(key)
    const qid = suggestNextQid(questions)
    const err = await createAndPlace(qid, spec.create, spec.validation, selectedQid)
    if (err) {
      setRowError(err)
      return
    }
    autoFocusRef.current = qid
  }

  const handleDuplicate = async (q: Question) => {
    setRowError(null)
    const qid = suggestNextQid(questions)
    // QuestionOption's named fields don't structurally satisfy
    // Record<string, unknown> (no index signature) — QuestionCreate accepts
    // arbitrary option dicts because it also serves callers building one by
    // hand; a question's own already-typed options are a valid instance of
    // that wider shape, just not one TS can see without help.
    const create = {
      text: q.text,
      type: q.type,
      options: q.options.map((o) => ({ ...o })),
      rows: q.rows ? q.rows.map((o) => ({ ...o })) : null,
      cols: q.cols ? q.cols.map((o) => ({ ...o })) : null,
      required: q.required,
      randomize_options: q.randomize_options,
    } as unknown as Omit<QuestionCreate, 'qid'>
    const validation = (q.validation as Record<string, unknown> | null) ?? undefined
    const err = await createAndPlace(qid, create, validation, q.qid)
    if (err) setRowError(err)
  }

  const handleDelete = async (qid: string) => {
    if (!studyId) return
    if (!window.confirm(`Delete ${qid}? This cannot be undone.`)) return
    setRowError(null)
    try {
      await questionnaireApi.deleteQuestion(studyId, qid)
      setQuestions((qs) => qs.filter((q) => q.qid !== qid))
      if (selectedQid === qid) setSelectedQid(null)
      void loadMap()
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

  const commitOrder = useCallback(
    async (next: Question[], previous: Question[]) => {
      if (!studyId) return
      setQuestions(next)
      setRowError(null)
      try {
        await questionnaireApi.reorderQuestions(studyId, next.map((q) => q.qid))
        void loadMap()
      } catch (err) {
        setQuestions(previous)
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
    },
    [studyId, loadMap],
  )

  const moveQid = useCallback(
    (qid: string, toIndex: number) => {
      const from = questions.findIndex((q) => q.qid === qid)
      const to = Math.max(0, Math.min(questions.length - 1, toIndex))
      if (from < 0 || from === to) return
      const next = questions.slice()
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      void commitOrder(next, questions)
    },
    [questions, commitOrder],
  )

  const onQuestionSaved = (oldQid: string, updated: Question) => {
    setQuestions((qs) => qs.map((q) => (q.qid === oldQid ? updated : q)))
    if (selectedQid === oldQid) setSelectedQid(updated.qid)
    void loadMap()
  }

  const selected = useMemo(
    () => questions.find((q) => q.qid === selectedQid) ?? null,
    [questions, selectedQid],
  )
  const selectedIndex = useMemo(
    () => (selected ? questions.findIndex((q) => q.qid === selected.qid) : -1),
    [questions, selected],
  )

  const priorQids = useMemo(
    () => (selectedIndex < 0 ? [] : questions.slice(0, selectedIndex).map((q) => q.qid)),
    [questions, selectedIndex],
  )
  const earlier = useMemo(
    () => (selectedIndex < 0 ? [] : questions.slice(0, selectedIndex)),
    [questions, selectedIndex],
  )

  // What the preview pane shows: the live draft standing in for whatever is
  // selected, everything else as last saved.
  const previewQuestions = useMemo(() => {
    if (previewAll) {
      return questions.map((q) => (draftQuestion && q.qid === selected?.qid ? draftQuestion : q))
    }
    return draftQuestion ? [draftQuestion] : selected ? [selected] : []
  }, [previewAll, questions, draftQuestion, selected])

  if (loading) return <LoadingSpinner />

  return (
    <div>
      <div className="sticky top-14 z-10 -mx-8 mb-6 border-b border-slate-200 bg-white/95 px-8 pb-4 pt-2 backdrop-blur">
        <Link to={`/projects/${studyId}`} className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to project
        </Link>
        <div className="flex items-center justify-between mt-1 flex-wrap gap-2">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold text-slate-900">Survey builder</h1>
            {status && status.source !== 'extracted' && (
              <span className="text-sm text-slate-400 tabular-nums">{questions.length} questions</span>
            )}
            {dirty && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800" role="status">
                Unsaved changes
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {status && status.source !== 'extracted' && questions.length > 0 && (
              <button
                type="button"
                onClick={() => setPreviewAll((v) => !v)}
                aria-pressed={previewAll}
                className={`text-xs font-medium px-3 py-1.5 rounded-md border transition-colors ${
                  previewAll
                    ? 'border-primary-500 bg-primary-50 text-primary-700'
                    : 'border-slate-300 text-slate-600 hover:border-slate-400'
                }`}
              >
                {previewAll ? 'Previewing whole survey' : 'Preview whole survey'}
              </button>
            )}
            {status && status.source !== 'extracted' && (
              <Link
                to={`/projects/${studyId}/questionnaire`}
                className="text-sm font-medium text-primary-500 hover:text-primary-600"
              >
                Ready for review →
              </Link>
            )}
          </div>
        </div>
        {status && (
          <p className="text-sm text-slate-500 mt-1">
            {status.title ?? status.source_filename} · status: <span className="font-medium">{status.status}</span>
          </p>
        )}
        {status && status.source !== 'extracted' && (
          <div className="mt-2 flex items-center gap-3 flex-wrap">
            <label htmlFor="survey-theme" className="text-xs text-slate-500">
              Theme
            </label>
            <ThemeSelect
              id="survey-theme"
              value={status.theme ?? DEFAULT_THEME_SLUG}
              onChange={handleThemeChange}
              disabled={!canWrite}
            />
            <span className="text-xs text-slate-400">Default look for respondents.</span>
            {themeMsg && (
              <span className={`text-xs ${themeMsg.ok ? 'text-emerald-600' : 'text-red-600'}`}>
                {themeMsg.text}
              </span>
            )}
            <span className="text-xs text-slate-400">
              Alt+↑ / Alt+↓ or drag reorders the focused question.
            </span>
          </div>
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
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          {/* List */}
          <div className="lg:col-span-3 bg-white rounded-lg border border-slate-200 shadow-panel overflow-hidden h-fit">
            {canWrite && (
              <div className="p-3 border-b border-slate-200">
                <AddQuestionPalette onPick={handleAddFromPalette} />
              </div>
            )}

            {rowError && (
              <p className="text-xs text-red-600 bg-red-50 border-b border-red-200 px-3 py-2">{rowError}</p>
            )}

            <div className="max-h-[65vh] overflow-y-auto dvv-cascade">
              {questions.map((q, i) => {
                const bucket = bucketInfo(map[q.qid]?.bucket)
                return (
                  <div
                    key={q.qid}
                    className={`relative flex items-center border-b border-slate-100 ${
                      q.qid === selectedQid ? 'bg-sky-50' : 'hover:bg-slate-50'
                    } ${dropBeforeQid === q.qid && dragQid !== q.qid ? 'border-t-2 border-t-sky-500' : ''}`}
                  >
                    {bucket && (
                      <span
                        aria-hidden
                        title={bucket.label}
                        className={`absolute left-0 top-2 bottom-2 w-1 rounded-r ${bucket.dot}`}
                      />
                    )}
                    <button
                      onClick={() => setSelectedQid(q.qid)}
                      draggable={canWrite}
                      onDragStart={() => setDragQid(q.qid)}
                      onDragOver={(e) => {
                        if (!dragQid) return
                        e.preventDefault()
                        setDropBeforeQid(q.qid)
                      }}
                      onDrop={() => {
                        if (dragQid && dragQid !== q.qid) moveQid(dragQid, i)
                        setDragQid(null)
                        setDropBeforeQid(null)
                      }}
                      onDragEnd={() => {
                        setDragQid(null)
                        setDropBeforeQid(null)
                      }}
                      onKeyDown={(e) => {
                        if (!canWrite || !e.altKey) return
                        if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                          e.preventDefault()
                          moveQid(q.qid, i + (e.key === 'ArrowUp' ? -1 : 1))
                        }
                      }}
                      title={canWrite ? 'Select. Drag, or Alt+↑ / Alt+↓, to reorder.' : undefined}
                      className={`flex-1 text-left pl-4 pr-1 py-2.5 min-w-0 ${canWrite ? 'cursor-grab active:cursor-grabbing' : ''}`}
                    >
                      <div className="flex items-center gap-2">
                        <TypeIcon type={q.type} className="h-3.5 w-3.5 text-slate-400 shrink-0" />
                        <span className="font-mono text-xs text-slate-700 shrink-0">{q.qid}</span>
                      </div>
                      <p className="text-sm text-slate-800 mt-1 truncate">
                        {q.text || <em className="text-slate-400">(no text)</em>}
                      </p>
                    </button>
                    {canWrite && (
                      <div className="flex items-center gap-0.5 pr-2 shrink-0">
                        <button
                          onClick={() => handleDuplicate(q)}
                          className="text-slate-400 hover:text-slate-700 px-1"
                          aria-label={`duplicate ${q.qid}`}
                          title="Duplicate"
                        >
                          ⧉
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
                )
              })}
              {questions.length === 0 && (
                <div className="p-4 text-sm text-slate-400">
                  {canWrite ? (
                    <p>No questions yet. Pick a card above to add your first one.</p>
                  ) : (
                    <p>No questions yet.</p>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Detail */}
          <div className="lg:col-span-5 bg-white rounded-lg border border-slate-200 shadow-panel p-6">
            {selected ? (
              <QuestionDetail
                key={selected.qid}
                studyId={studyId!}
                q={selected}
                priorQids={priorQids}
                earlier={earlier}
                entry={map[selected.qid]}
                canWrite={canWrite}
                autoFocusText={autoFocusRef.current === selected.qid}
                onSaved={onQuestionSaved}
                onGoTo={setSelectedQid}
                onDirtyChange={setDirty}
                onDraftChange={setDraftQuestion}
                onAutoFocusConsumed={() => { autoFocusRef.current = null }}
              />
            ) : (
              <p className="text-sm text-slate-400">
                {questions.length === 0
                  ? 'Pick a question type on the left to start building.'
                  : 'Select a question to edit it, or add a new one.'}
              </p>
            )}
          </div>

          {/* Live respondent preview */}
          <div className="lg:col-span-4 bg-white rounded-lg border border-slate-200 shadow-panel p-6 h-fit lg:sticky lg:top-32">
            <h2 className="text-xs font-medium uppercase tracking-wide text-slate-500 mb-3">
              {previewAll ? 'Respondent preview — whole survey' : 'Respondent preview'}
            </h2>
            <RespondentPreview questions={previewQuestions} />
          </div>
        </div>
      )}
    </div>
  )
}
