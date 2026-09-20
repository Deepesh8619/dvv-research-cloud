import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import axios from 'axios'
import {
  Question,
  QuestionMapEntry,
  QuestionnaireStatus,
  QUESTION_TYPES,
  questionnaireApi,
  QuestionOption,
  QuestionSplitItem,
  UnresolvedQuestion,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import LoadingSpinner from '../components/LoadingSpinner'
import { TableSkeleton } from '../components/Skeleton'
import RoutingEditor, { FlatRouting, fromFlatRouting, toFlatRouting } from '../components/RoutingEditor'
import { flagInfo } from '../components/questionnaireFlags'
import CorrectionBox from '../components/CorrectionBox'
import ExtractionProvenance from '../components/ExtractionProvenance'
import TeachFormat from '../components/TeachFormat'
import { useLabel } from '../labels/LabelContext'
import { BucketBar, ConnectionsPanel, IssuesPanel, ProgressRing } from '../components/review/QuestionMap'
import { bucketInfo } from '../lib/buckets'
import { typeHint, typeLabel } from '../labels/questionTypes'
import { canCorrect as hasCorrectRole, canWrite as hasWriteRole } from '../auth/roles'

// Roles allowed to write (matches api/auth.py require_role(OWNER, ANALYST)).

// The type picker offers "Adds up to (constant sum)" right next to "Number" —
// it is a UI-only pseudo-type layered on QUESTION_TYPES (the real wire
// vocabulary, shared with the builder), never added there, since it saves as
// numeric + rows + validation.sum rather than a type of its own.
const TYPE_CHOICES: string[] = QUESTION_TYPES.flatMap((t) =>
  t === 'numeric' ? [t, 'constant_sum'] : [t],
)

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
  entry,
  selected,
  onClick,
  draggable,
  dragging,
  dropBefore,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  onMoveKey,
}: {
  q: Question
  entry?: QuestionMapEntry
  selected: boolean
  onClick: () => void
  draggable: boolean
  dragging: boolean
  dropBefore: boolean
  onDragStart: () => void
  onDragOver: (e: React.DragEvent) => void
  onDrop: () => void
  onDragEnd: () => void
  onMoveKey: (delta: number) => void
}) {
  const bucket = bucketInfo(entry?.bucket)
  const links = entry?.links
  const summary = [
    links?.asked_if.length ? `asked if ${links.asked_if.join(', ')}` : '',
    links?.options_from.length ? `options from ${links.options_from.join(', ')}` : '',
    links?.text_from.length ? `pipes ${links.text_from.join(', ')}` : '',
    links?.used_by.length ? `decides ${links.used_by.length}` : '',
  ].filter(Boolean)
  return (
    <button
      id={`qrow-${q.qid}`}
      onClick={onClick}
      draggable={draggable}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
      // Dragging is not usable by keyboard or screen reader, so the same
      // reorder is reachable with Alt+Arrow on the focused row.
      onKeyDown={(e) => {
        if (!draggable || !e.altKey) return
        if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
          e.preventDefault()
          onMoveKey(e.key === 'ArrowUp' ? -1 : 1)
        }
      }}
      title={draggable ? 'Drag to reorder, or Alt+↑ / Alt+↓' : undefined}
      className={`relative w-full text-left pl-4 pr-3 py-2.5 border-b border-slate-100 transition-all duration-200 ${
        selected ? 'bg-indigo-50/70 shadow-[inset_3px_0_0_0_#6366f1]' : 'hover:bg-slate-50'
      } ${dragging ? 'opacity-40' : ''} ${
        dropBefore ? 'border-t-2 border-t-sky-500' : ''
      } ${draggable ? 'cursor-grab active:cursor-grabbing' : ''}`}
    >
      {bucket && (
        <span
          aria-hidden
          title={bucket.label}
          className={`absolute left-0 top-2.5 bottom-2.5 w-1 rounded-r ${bucket.dot} ${selected ? 'opacity-0' : 'opacity-70'}`}
        />
      )}
      <div className="flex items-center gap-2">
        <ConfidenceDot conf={q.extraction_confidence} />
        <span className="font-mono text-xs text-slate-700 shrink-0">{q.qid}</span>
        <span className="text-xs text-slate-400 shrink-0">{typeLabel(q.type)}</span>
        {q.approved && (
          <span className="text-[11px] text-emerald-600 shrink-0" title="Approved">
            ✓ approved
          </span>
        )}
      </div>
      <p className="text-sm text-slate-800 mt-1 truncate">{q.text || <em className="text-slate-400">(no text)</em>}</p>
      {summary.length > 0 && (
        <p className="mt-1 truncate text-[11px] text-slate-400">↳ {summary.join(' · ')}</p>
      )}
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
  // 'constant_sum' is a UI-only pseudo-type: see isConstantSum below.
  type: string
  options: QuestionOption[]
  // Constant-sum rows (one box per item) and the total they must reach.
  rows: QuestionOption[]
  sumTotal: number
  showIf: FlatRouting
  terminateIf: FlatRouting
  showIfVerbatim: string | null
  terminateIfVerbatim: string | null
}

// A numeric question WITH rows is what the backend calls a constant sum (see
// src/engines/questionnaire/constant_sum.py) — a numeric question with no
// rows is just a plain number and stays that way.
function isConstantSumQuestion(q: Question): boolean {
  return q.type === 'numeric' && (q.rows?.length ?? 0) > 0
}

function buildDetailState(q: Question): DetailState {
  const sum = q.validation?.sum
  return {
    text: q.text,
    type: isConstantSumQuestion(q) ? 'constant_sum' : q.type,
    options: q.options.map((o) => ({ ...o })),
    rows: (q.rows ?? []).map((o) => ({ ...o })),
    sumTotal: typeof sum === 'number' ? sum : 100,
    showIf: toFlatRouting(q.show_if),
    terminateIf: toFlatRouting(q.terminate_if),
    showIfVerbatim: q.show_if?.verbatim_text ?? null,
    terminateIfVerbatim: q.terminate_if?.verbatim_text ?? null,
  }
}

// --- Re-read with current parser ---------------------------------------

/** Re-runs the deterministic parsers on this question's own source text — no
 * model call. A stored question was parsed by whatever parser code existed
 * the day it was extracted; this picks up a fix since then for free. */
function RereadButton({
  studyId,
  qid,
  onChanged,
}: {
  studyId: string
  qid: string
  onChanged: (updated: Question) => void
}) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const run = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const { changed, ...updated } = (await questionnaireApi.reread(studyId, qid)).data
      onChanged(updated as Question)
      setMsg(`Updated: ${changed.join(', ')}`)
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 422) {
        setMsg('No improvement found — left unchanged.')
      } else {
        setMsg(errorMessage(err) || 'Re-read failed')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={run}
        disabled={busy}
        className="text-xs font-medium text-sky-700 hover:text-sky-900 hover:underline disabled:opacity-50"
      >
        {busy ? 'Re-reading…' : 'Re-read with current parser'}
      </button>
      {msg && <span className="text-xs text-slate-500">{msg}</span>}
    </span>
  )
}

// --- Split by... ----------------------------------------------------------

interface SplitItemState {
  label: string
  qid: string
  codes: string
}

/** Labels pre-filled from whatever list the question already has — a grid's
 * rows/cols if it is one, else its own options — so splitting a carousel-
 * shaped question ("for each category...") starts from what it already says. */
function defaultSplitItems(q: Question): SplitItemState[] {
  const source = (q.rows?.length ? q.rows : q.cols?.length ? q.cols : q.options) || []
  if (source.length >= 2) {
    return source.map((o) => ({ label: o.label, qid: '', codes: '' }))
  }
  return [
    { label: '', qid: '', codes: '' },
    { label: '', qid: '', codes: '' },
  ]
}

/** "Q6. for each category, which brands" -> Q6_1..Q6_n, one real question per
 * item. The "asked if" column is a plain qid + codes pair rather than the full
 * RoutingEditor — this only ever needs one leaf condition per item, and N
 * embedded routing editors would be a lot of state for that. */
function SplitPanel({
  studyId,
  q,
  onSplit,
}: {
  studyId: string
  q: Question
  onSplit: (qids: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<SplitItemState[]>(() => defaultSplitItems(q))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const setItem = (i: number, patch: Partial<SplitItemState>) =>
    setItems((s) => s.map((it, idx) => (idx === i ? { ...it, ...patch } : it)))
  const addItem = () => setItems((s) => [...s, { label: '', qid: '', codes: '' }])
  const removeItem = (i: number) => setItems((s) => s.filter((_, idx) => idx !== i))

  const validLabels = items.filter((it) => it.label.trim()).length

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const payload: QuestionSplitItem[] = items
        .filter((it) => it.label.trim())
        .map((it) => {
          const codes = it.codes
            .split(',')
            .map((c) => c.trim())
            .filter(Boolean)
            .map((c) => (Number.isNaN(Number(c)) ? c : Number(c)))
          return {
            label: it.label.trim(),
            show_if: it.qid.trim() && codes.length ? { op: 'in', qid: it.qid.trim(), values: codes } : null,
          }
        })
      const res = await questionnaireApi.split(studyId, q.qid, payload)
      setOpen(false)
      onSplit(res.data.into)
    } catch (err) {
      setError(errorMessage(err) || 'Split failed')
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => {
          setItems(defaultSplitItems(q))
          setOpen(true)
        }}
        className="text-xs font-medium text-sky-700 hover:text-sky-900 hover:underline"
      >
        Split by…
      </button>
    )
  }

  return (
    <div className="mt-3 border border-sky-200 bg-sky-50/40 rounded-md p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h4 className="text-sm font-semibold text-slate-800">
          Split into one question per item
        </h4>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-xs text-slate-400 hover:text-slate-600"
        >
          Cancel
        </button>
      </div>
      <div className="space-y-2">
        {items.map((it, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              value={it.label}
              onChange={(e) => setItem(i, { label: e.target.value })}
              placeholder="label"
              className="h-8 flex-1 px-2 rounded-md border border-slate-300 text-sm"
              aria-label="item label"
            />
            <input
              value={it.qid}
              onChange={(e) => setItem(i, { qid: e.target.value })}
              placeholder="asked if qid"
              className="h-8 w-28 px-2 rounded-md border border-slate-300 text-sm"
              aria-label="asked if qid"
            />
            <input
              value={it.codes}
              onChange={(e) => setItem(i, { codes: e.target.value })}
              placeholder="codes e.g. 1,2"
              className="h-8 w-28 px-2 rounded-md border border-slate-300 text-sm"
              aria-label="asked if codes"
            />
            {items.length > 2 && (
              <button
                type="button"
                onClick={() => removeItem(i)}
                className="text-slate-400 hover:text-red-600 px-1"
                aria-label="remove item"
              >
                ✕
              </button>
            )}
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={addItem}
        className="text-xs text-primary-500 hover:text-primary-600 font-medium"
      >
        + Add item
      </button>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="pt-2 border-t border-slate-100">
        <button
          type="button"
          onClick={submit}
          disabled={busy || validLabels < 2}
          className="bg-primary-500 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-primary-600 disabled:opacity-50"
        >
          {busy ? 'Splitting…' : `Split into ${validLabels || '…'} questions`}
        </button>
      </div>
    </div>
  )
}

// Exported for tests. The option-code and routing inputs hold non-obvious
// draft state across keystrokes, and that behaviour is only observable by
// driving a real input — not from the page's props.
export function QuestionDetail({
  studyId,
  q,
  priorQids,
  canWrite,
  canCorrect,
  profile,
  onSaved,
  onApproved,
  onSplit,
}: {
  studyId: string
  q: Question
  priorQids: string[]
  canWrite: boolean
  /** Founders' lane — the correction surface renders only for them. */
  canCorrect: boolean
  profile?: { slug?: string; label?: string } | null
  onSaved: (updated: Question) => void
  onApproved: (qid: string, clearedFlags?: string[]) => void
  /** After a successful split: reload the list and select the first copy. */
  onSplit: (qids: string[]) => void
}) {
  const [state, setState] = useState<DetailState>(() => buildDetailState(q))
  // Raw text of an option-code box while it has focus — see the input below.
  const [codeDrafts, setCodeDrafts] = useState<Record<number, string>>({})
  // Display-only fallback: the extractor stashes the routing sentence in
  // question.validation.routing_verbatim (one shared string) so it survives even
  // when the structured show_if/terminate_if is nulled out as unresolvable — the
  // exact ambiguous case. It is NOT wired into `state`/handleSave, so it never
  // gets written back onto a specific show_if/terminate_if expression on the next
  // PATCH; it only enriches the verbatim panel below.
  const routingVerbatim =
    typeof q.validation?.routing_verbatim === 'string' ? q.validation.routing_verbatim : null
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
  const isConstantSum = state.type === 'constant_sum'

  const setOption = (i: number, patch: Partial<QuestionOption>) =>
    set('options', state.options.map((o, idx) => (idx === i ? { ...o, ...patch } : o)))
  const addOption = () => {
    const nextCode = state.options.reduce((m, o) => Math.max(m, o.code), 0) + 1
    set('options', [...state.options, { code: nextCode, label: '' }])
  }
  const removeOption = (i: number) => set('options', state.options.filter((_, idx) => idx !== i))

  // Rows are re-coded 1..n on every change — order IS the code, so there is
  // nothing for a reviewer to get wrong by hand.
  const setRowLabel = (i: number, label: string) =>
    set('rows', state.rows.map((r, idx) => (idx === i ? { ...r, label } : r)))
  const addRow = () => set('rows', [...state.rows, { code: state.rows.length + 1, label: '' }])
  const removeRow = (i: number) =>
    set(
      'rows',
      state.rows.filter((_, idx) => idx !== i).map((r, idx) => ({ ...r, code: idx + 1 })),
    )

  const handleSave = async () => {
    setSaving(true)
    setProblems([])
    setError(null)
    setSavedOk(false)
    try {
      const showIf = fromFlatRouting(state.showIf, state.showIfVerbatim)
      const terminateIf = fromFlatRouting(state.terminateIf, state.terminateIfVerbatim)
      const patch: Record<string, unknown> = {
        text: state.text,
        // The pseudo-type never reaches the API — what is saved is always the
        // real wire type (numeric), with the rows/total carrying the meaning.
        type: isConstantSum ? 'numeric' : state.type,
        options: hasOptions
          ? state.options.map((o) => ({ code: o.code, label: o.label }))
          : [],
        show_if: (showIf as unknown as Record<string, unknown>) ?? null,
        terminate_if: (terminateIf as unknown as Record<string, unknown>) ?? null,
      }
      if (isConstantSum) {
        patch.rows = state.rows.map((r) => ({ code: r.code, label: r.label }))
        patch.validation = { min: 0, max: state.sumTotal, sum: state.sumTotal }
      } else if (isConstantSumQuestion(q)) {
        // Leaving constant-sum for another type: a plain numeric (or anything
        // else) has no per-item boxes, so the stale rows must not survive.
        patch.rows = null
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

  const openBlockingFlag = q.flags.some((f) => flagInfo(f).blocking)

  const handleApprove = async () => {
    setApproving(true)
    setError(null)
    try {
      const res = await questionnaireApi.approve(studyId, q.qid)
      onApproved(q.qid, (res.data as { cleared_flags?: string[] })?.cleared_flags ?? [])
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
        <div className="flex items-center gap-3">
          {canWrite && <RereadButton studyId={studyId} qid={q.qid} onChanged={onSaved} />}
          {canWrite && <SplitPanel studyId={studyId} q={q} onSplit={onSplit} />}
          {canCorrect && (
            <CorrectionBox
              studyId={studyId}
              questionId={q.qid}
              profileSlug={profile?.slug}
              profileLabel={profile?.label}
            />
          )}
        </div>
      </div>

      {/* Teaching is only meaningful once we know WHICH format we would be
          teaching — an unrecognised document has no profile to attach it to. */}
      <div>
        {canCorrect && profile?.slug && (
          <TeachFormat
            profileSlug={profile.slug}
            profileLabel={profile.label}
            question={q}
          />
        )}
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

      <ExtractionProvenance method={q.extraction_method} evidence={q.extraction_evidence} />

      {/* A derived variable is computed by the scripter from other answers, so
          there is no wording, type or routing to review. Saying so plainly beats
          leaving a reviewer to work out why every field below is blank. */}
      {q.flags.includes('DERIVED_VARIABLE') && (
        <div className="text-sm rounded-md px-3 py-2 bg-slate-100 text-slate-700 border border-slate-200">
          <span className="font-medium">Not asked to respondents.</span>
          <span className="block mt-0.5">
            This is a value the scripter calculates from other answers. Nothing here is shown in
            the survey, so the fields below are expected to be empty — approve it to move on.
          </span>
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
          {TYPE_CHOICES.map((t) => (
            <option key={t} value={t}>
              {typeLabel(t)}
            </option>
          ))}
        </select>
        {/* The name alone is not enough to review against — say what the
            respondent actually does, so a wrong type is obvious on sight. */}
        <p className="mt-1 text-xs text-slate-500">{typeHint(state.type)}</p>
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
                  value={codeDrafts[i] ?? String(o.code)}
                  disabled={!canWrite}
                  // Clearing the box gives "", and Number("") is 0 — so
                  // backspacing to retype a code silently wrote code 0 into the
                  // questionnaire. Hold the raw text while editing, commit only
                  // a real number, and restore the stored code on blur.
                  onChange={(e) => {
                    const raw = e.target.value
                    setCodeDrafts((d) => ({ ...d, [i]: raw }))
                    if (raw.trim() !== '' && Number.isFinite(Number(raw))) {
                      setOption(i, { code: Number(raw) })
                    }
                  }}
                  onBlur={() =>
                    setCodeDrafts((d) => {
                      const next = { ...d }
                      delete next[i]
                      return next
                    })
                  }
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

      {/* Constant sum: one line per item, plus the total they must reach.
          Saved as numeric + rows + validation.sum — see handleSave. */}
      {isConstantSum && (
        <div>
          <span className="text-xs font-medium uppercase tracking-wide text-slate-500 block mb-2">
            Items
          </span>
          <div className="space-y-2">
            {state.rows.map((r, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="h-8 w-8 shrink-0 flex items-center justify-center text-xs text-slate-400 font-mono">
                  {r.code}
                </span>
                <input
                  value={r.label}
                  disabled={!canWrite}
                  onChange={(e) => setRowLabel(i, e.target.value)}
                  placeholder="item"
                  className="h-8 flex-1 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
                  aria-label="item label"
                />
                {canWrite && (
                  <button
                    type="button"
                    onClick={() => removeRow(i)}
                    className="text-slate-400 hover:text-red-600 px-1"
                    aria-label="remove item"
                  >
                    ✕
                  </button>
                )}
              </div>
            ))}
            {state.rows.length === 0 && <p className="text-sm text-slate-400">No items.</p>}
          </div>
          {canWrite && (
            <button
              type="button"
              onClick={addRow}
              className="mt-2 text-xs text-primary-500 hover:text-primary-600 font-medium"
            >
              + Add item
            </button>
          )}
          <label className="block mt-3">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Must total
            </span>
            <input
              type="number"
              value={state.sumTotal}
              disabled={!canWrite}
              onChange={(e) => set('sumTotal', Number(e.target.value) || 0)}
              className="mt-1 h-8 w-24 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
            />
          </label>
        </div>
      )}

      {/* Routing: show_if & terminate_if, each beside its verbatim source.
          show_if excludes the question itself (it can't gate its own display);
          terminate_if INCLUDES it — "terminate if S1 = 3" on S1 is the
          canonical screener pattern and validate() explicitly allows it. */}
      <RoutingBlock
        label="Show this question if"
        emptyText="No condition — everyone sees this question."
        flat={state.showIf}
        verbatim={state.showIfVerbatim ?? routingVerbatim}
        priorQids={priorQids.filter((qid) => qid !== q.qid)}
        canWrite={canWrite}
        onChangeFlat={(f) => set('showIf', f)}
      />
      <RoutingBlock
        label="Terminate / screen out if"
        emptyText="No screen-out — nobody is disqualified here."
        flat={state.terminateIf}
        verbatim={state.terminateIfVerbatim ?? routingVerbatim}
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
            // An approved question can pick up a blocking flag later — a re-read,
            // a split, an edited route. Approving is what clears one, and a
            // disabled "Approved" left it stuck: Finalize refused forever.
            disabled={approving || (q.approved && !openBlockingFlag)}
            title={openBlockingFlag && q.approved ? 'Confirms this question again and clears its blocking flags' : undefined}
            className="bg-emerald-600 text-white text-sm font-medium px-4 py-2 rounded-md hover:bg-emerald-700 disabled:opacity-50"
          >
            {approving
              ? 'Approving…'
              : q.approved
                ? openBlockingFlag ? 'Re-approve (clears flags)' : 'Approved'
                : 'Approve'}
          </button>
        </div>
      )}
    </div>
  )
}

function RoutingBlock({
  label,
  emptyText,
  flat,
  verbatim,
  priorQids,
  canWrite,
  onChangeFlat,
}: {
  label: string
  emptyText: string
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
      <RoutingEditor
        label={label}
        emptyText={emptyText}
        flat={flat}
        onChange={onChangeFlat}
        qidOptions={priorQids}
        disabled={!canWrite}
      />
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
  const tTitle = useLabel('questionnaire.review.title', 'Questionnaire review')
  const tEmpty = useLabel('questionnaire.review.empty', 'Select a question to review its details.')
  const canWrite = hasWriteRole(user?.role)
  // The founders' correction lane. Mirrors the server-side gate in
  // routes/corrections.py — the server refuses regardless, this only decides
  // whether the affordance renders.
  const canCorrect = hasCorrectRole(user?.role)

  const [status, setStatus] = useState<QuestionnaireStatus | null>(null)
  const [questions, setQuestions] = useState<Question[]>([])
  // Reorder. The server rejects an order that moves a question ahead of one its
  // routing depends on (a forward reference), so the optimistic list is rolled
  // back and the server's reason shown rather than left silently wrong.
  const [dragQid, setDragQid] = useState<string | null>(null)
  const [dropBeforeQid, setDropBeforeQid] = useState<string | null>(null)
  const [reorderError, setReorderError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [selectedQid, setSelectedQid] = useState<string | null>(null)
  const [approveAllError, setApproveAllError] = useState<UnresolvedQuestion[] | null>(null)
  const [approvingAll, setApprovingAll] = useState(false)
  const [approvedOk, setApprovedOk] = useState(false)
  const [approvingClean, setApprovingClean] = useState(false)
  const [cleanMsg, setCleanMsg] = useState<string | null>(null)
  const [rereadingAll, setRereadingAll] = useState(false)
  const [rereadMsg, setRereadMsg] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  // Each question's bucket and links, from the server's reading of the
  // definition. Refreshed after any edit, since an edit can change a link.
  const [map, setMap] = useState<Record<string, QuestionMapEntry>>({})
  const [bucket, setBucket] = useState('all')
  // "?q=Q2c" — arriving from the pre-link check, straight to the question.
  const [search] = useSearchParams()
  const wanted = search.get('q')

  const loadStatus = useCallback(async () => {
    if (!studyId) return
    try {
      const res = await questionnaireApi.status(studyId)
      setStatus(res.data)
      setNotFound(false)
      setLoadError(null)
      return res.data
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        setNotFound(true)
        setStatus(null)
        setLoadError(null)
      } else {
        // Any other failure (500, network, auth) must not render a silent
        // blank page — show it so the user isn't staring at nothing.
        setStatus(null)
        setLoadError(errorMessage(err) || 'Could not load the questionnaire. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }, [studyId])

  const loadMap = useCallback(async () => {
    if (!studyId) return
    try {
      const res = await questionnaireApi.map(studyId)
      setMap(res.data.questions)
    } catch {
      /* the list still works without it — buckets and links just stay hidden */
    }
  }, [studyId])

  const loadQuestions = useCallback(async () => {
    if (!studyId) return
    try {
      const res = await questionnaireApi.questions(studyId)
      setQuestions(res.data.questions)
      void loadMap()
    } catch {
      /* still processing / none — status drives the UI */
    }
  }, [studyId, loadMap])

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

  // How long since the saved question count last moved. This is what makes
  // "still working" distinguishable from "died quietly" without asking anyone
  // to read a server log.
  const seenCount = useRef<number | null>(null)
  const lastChangeAt = useRef<number>(Date.now())
  const [progressAge, setProgressAge] = useState<number | null>(null)

  useEffect(() => {
    if (status?.status !== 'processing') {
      setProgressAge(null)
      return
    }
    if (seenCount.current !== status.n_questions) {
      seenCount.current = status.n_questions
      lastChangeAt.current = Date.now()
    }
    const t = setInterval(
      () => setProgressAge(Math.floor((Date.now() - lastChangeAt.current) / 1000)),
      1000,
    )
    return () => clearInterval(t)
  }, [status?.status, status?.n_questions])

  const selected = useMemo(
    () => questions.find((q) => q.qid === selectedQid) ?? null,
    [questions, selectedQid],
  )

  const onQuestionSaved = (updated: Question) => {
    setQuestions((qs) => qs.map((q) => (q.qid === updated.qid ? updated : q)))
    void loadMap() // routing or a list may have changed a link
  }

  // The split copies replace the original in the list at the server; reload
  // rather than patch locally, then land on the first copy.
  const onQuestionSplit = async (qids: string[]) => {
    await loadQuestions()
    if (qids[0]) setSelectedQid(qids[0])
  }

  // Go to a question from anywhere — an issue, a link chip — even if the
  // bucket filter is hiding it.
  const goTo = useCallback(
    (qid: string) => {
      if (!questions.some((q) => q.qid === qid)) return
      if (bucket !== 'all' && map[qid]?.bucket !== bucket) setBucket('all')
      setSelectedQid(qid)
      // Scroll the LIST only: scrollIntoView also moved every scrolling
      // ancestor, and pushed the app's own header off the screen.
      window.setTimeout(() => {
        const row = document.getElementById(`qrow-${qid}`)
        const list = row?.closest('[data-question-list]') as HTMLElement | null
        if (row && list) {
          list.scrollTo({ top: row.offsetTop - list.clientHeight / 2 + row.clientHeight / 2, behavior: 'smooth' })
        }
      }, 60)
    },
    [questions, bucket, map],
  )

  useEffect(() => {
    if (wanted && questions.some((q) => q.qid === wanted)) goTo(wanted)
    // Once, when the list arrives — not on every later edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, questions.length > 0])

  const bucketCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const q of questions) {
      const key = map[q.qid]?.bucket
      if (key) counts[key] = (counts[key] ?? 0) + 1
    }
    return counts
  }, [questions, map])
  const visible = bucket === 'all' ? questions : questions.filter((q) => map[q.qid]?.bucket === bucket)
  const onQuestionApproved = (qid: string, clearedFlags: string[] = []) => {
    // Approving clears the question's blocking flags on the server; drop them
    // here too, or the list still shows a flag the reviewer just resolved.
    setQuestions((qs) =>
      qs.map((q) =>
        q.qid === qid
          ? { ...q, approved: true, flags: q.flags.filter((f) => !clearedFlags.includes(f)) }
          : q,
      ),
    )
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

  const handleApproveClean = async () => {
    if (!studyId) return
    setApprovingClean(true)
    setCleanMsg(null)
    setApproveAllError(null)
    try {
      const res = await questionnaireApi.approveClean(studyId)
      const { n_newly_approved, still_blocked } = res.data
      await loadQuestions()
      await loadStatus()
      if (still_blocked.length > 0) {
        setCleanMsg(
          `Approved ${n_newly_approved} clean question${n_newly_approved === 1 ? '' : 's'}. ` +
            `${still_blocked.length} still need${still_blocked.length === 1 ? 's' : ''} manual review (flagged).`,
        )
      } else {
        setCleanMsg(
          `Approved ${n_newly_approved} question${n_newly_approved === 1 ? '' : 's'}. ` +
            `Nothing left flagged — you can finalize.`,
        )
      }
    } catch (err) {
      setCleanMsg(errorMessage(err) || 'Bulk approve failed')
    } finally {
      setApprovingClean(false)
    }
  }

  const handleRereadAll = async () => {
    if (!studyId) return
    setRereadingAll(true)
    setRereadMsg(null)
    try {
      const res = await questionnaireApi.rereadAll(studyId)
      await loadQuestions()
      setRereadMsg(
        res.data.n_updated > 0
          ? `Re-read ${res.data.n_questions} questions — ${res.data.n_updated} updated.`
          : `Re-read ${res.data.n_questions} questions — nothing to update.`,
      )
    } catch (err) {
      setRereadMsg(errorMessage(err) || 'Re-read all failed')
    } finally {
      setRereadingAll(false)
    }
  }

  const commitOrder = useCallback(
    async (next: Question[], previous: Question[]) => {
      if (!studyId) return
      setQuestions(next)
      setReorderError(null)
      try {
        await questionnaireApi.reorderQuestions(
          studyId,
          next.map((q) => q.qid),
        )
      } catch (err) {
        setQuestions(previous)
        const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
        setReorderError(
          typeof detail === 'string'
            ? detail
            : (detail?.error ?? 'Could not save the new order.'),
        )
      }
    },
    [studyId],
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

  if (loading) {
    return (
      <div>
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-slate-900">{tTitle}</h1>
          <p className="text-sm text-slate-500 mt-1">Loading questionnaire…</p>
        </div>
        <TableSkeleton rows={8} cols={2} />
      </div>
    )
  }

  const nApproved = questions.filter((q) => q.approved).length

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <Link to={`/projects/${studyId}`} className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to project
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-4">
          {status && status.n_questions > 0 && <ProgressRing done={nApproved} total={status.n_questions} />}
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{tTitle}</h1>
            {status && (
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-slate-500">
                <span className="truncate">{status.title ?? status.source_filename}</span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
                    status.status === 'approved'
                      ? 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200'
                      : 'bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200'
                  }`}
                >
                  {status.status.replace('_', ' ')}
                </span>
                {status.n_questions > 0 && (
                  <span className="tabular-nums">{nApproved} of {status.n_questions} approved</span>
                )}
              </p>
            )}
            {status?.profile && (
              <p className="text-xs text-slate-400 mt-0.5">
                Detected format: {status.profile.label} ({(status.profile.confidence * 100).toFixed(0)}%)
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Status load failed for a reason other than "no questionnaire yet" */}
      {loadError && (
        <div className="bg-red-50 border border-red-200 rounded-md p-4 max-w-xl">
          <p className="text-sm font-medium text-red-700 mb-1">Couldn&apos;t load questionnaire status</p>
          <p className="text-sm text-red-600">{loadError}</p>
          <button
            onClick={() => { setLoading(true); loadStatus() }}
            className="mt-3 text-sm font-medium text-red-700 underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* No questionnaire yet OR error → upload */}
      {!loadError && (notFound || status?.status === 'error') && (
        <div className="space-y-4 max-w-xl">
          {status?.status === 'error' && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-3 space-y-1">
              <p className="font-medium">Extraction failed for the last upload. You can upload again.</p>
              {status.error_detail && (
                <p className="text-red-500">
                  <span className="font-medium">Reason:</span> {status.error_detail}
                </p>
              )}
            </div>
          )}
          <UploadPanel studyId={studyId!} onUploaded={() => { setLoading(true); loadStatus() }} disabled={!canWrite} />
          {canWrite && (
            <p className="text-sm text-slate-500">
              Don&apos;t have a document?{' '}
              <Link to={`/projects/${studyId}/builder`} className="text-primary-500 hover:text-primary-600 font-medium">
                Build one from scratch
              </Link>
              .
            </p>
          )}
        </div>
      )}

      {/* Processing.

          A bare spinner could not answer the only question a researcher has
          while they wait — "is this working, or has it died?" Extraction
          checkpoints its progress, so the count below is real saved work; if
          it is still climbing the run is healthy, and if it has been still for
          minutes something is wrong. Showing nothing threw away the very thing
          checkpointing exists to provide. */}
      {status?.status === 'processing' && (
        <div className="bg-white rounded-lg border border-slate-200 p-8 text-center">
          <LoadingSpinner />
          {status.n_questions === 0 ? (
            <>
              <p className="text-sm text-slate-700 mt-3 font-medium">Preparing the document</p>
              {/* Before the first checkpoint there is genuinely nothing saved
                  yet: a whole-document pass runs first to catch questions the
                  splitter missed. A stall here is expected, so this must NOT
                  warn — the warning below is only honest once questions have
                  started landing. */}
              <p className="text-xs text-slate-500 mt-1">
                Checking for questions the splitter may have missed. This first
                step runs once and can take a few minutes.
              </p>
            </>
          ) : (
            <>
              <p className="text-sm text-slate-700 mt-3 font-medium">
                Reading the document — {status.n_questions} questions saved so far
              </p>
              <p className="text-xs text-slate-500 mt-1">
                {progressAge !== null && progressAge >= 120
                  ? `No new questions for ${Math.floor(progressAge / 60)}m — the model ` +
                    'backend may be rate-limited. Everything already read is saved.'
                  : 'Questions are saved as they are read, so nothing is lost if this is interrupted.'}
              </p>
            </>
          )}
          {/* Whether the script's own record is helping. Half the model calls
              ride on this, and a reviewer watching a slow run otherwise cannot
              tell an accelerated run from one paying full price. */}
          <p className="text-xs text-slate-400 mt-2">
            {status.used_datamap
              ? 'Using this project’s data map, so questions it already describes cost no AI reading.'
              : 'No data map on this project yet — every unresolved question needs an AI reading. ' +
                'Uploading the data file first makes this roughly twice as fast.'}
          </p>
        </div>
      )}

      {/* Ready → list + detail */}
      {status && !notFound && !['processing', 'error'].includes(status.status) && (
        <>
          <IssuesPanel errors={status.validation_errors} onGoTo={goTo} />

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
          {cleanMsg && (
            <div className="bg-sky-50 border border-sky-200 rounded-md p-3 mb-4 text-sm text-sky-800">
              {cleanMsg}
            </div>
          )}
          {rereadMsg && (
            <div className="bg-sky-50 border border-sky-200 rounded-md p-3 mb-4 text-sm text-sky-800">
              {rereadMsg}
            </div>
          )}
          {approvedOk && (
            <div className="bg-emerald-50 border border-emerald-200 rounded-md p-3 mb-4 text-sm text-emerald-700">
              Questionnaire approved.
            </div>
          )}

          <div className="grid grid-cols-5 gap-6">
            {/* List */}
            <div className="col-span-2 bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden h-fit">
              <div className="px-3 py-2.5 border-b border-slate-200 flex items-center justify-between">
                <span className="text-sm font-semibold text-slate-800 tabular-nums">
                  {bucket === 'all' ? `${questions.length} questions` : `${visible.length} of ${questions.length}`}
                </span>
                {canWrite && (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleRereadAll}
                      disabled={rereadingAll || questions.length === 0}
                      title="Re-run the current parser on every question — no model call, so parser fixes reach old questions for free"
                      className="text-xs bg-white border border-slate-300 text-slate-700 font-medium px-3 py-1.5 rounded-md hover:bg-slate-50 disabled:opacity-50"
                    >
                      {rereadingAll ? 'Re-reading…' : 'Re-read all'}
                    </button>
                    <button
                      onClick={handleApproveClean}
                      disabled={approvingClean || questions.length === 0}
                      title="Approve every question that has no blocking flag, so you only hand-review the flagged ones"
                      className="text-xs bg-white border border-emerald-600 text-emerald-700 font-medium px-3 py-1.5 rounded-md hover:bg-emerald-50 disabled:opacity-50"
                    >
                      {approvingClean ? 'Approving…' : 'Approve remaining'}
                    </button>
                    <button
                      onClick={handleApproveAll}
                      disabled={approvingAll || questions.length === 0}
                      title="Finalize the whole questionnaire — requires every question approved and unflagged"
                      className="text-xs bg-emerald-600 text-white font-medium px-3 py-1.5 rounded-md hover:bg-emerald-700 disabled:opacity-50"
                    >
                      {approvingAll ? 'Finalizing…' : 'Finalize'}
                    </button>
                  </div>
                )}
              </div>
              {Object.keys(bucketCounts).length > 0 && (
                <div className="border-b border-slate-100 bg-slate-50/60">
                  <BucketBar counts={bucketCounts} total={questions.length} value={bucket} onChange={setBucket} />
                  {bucket !== 'all' && (
                    <p className="px-3 pb-2 text-[11px] text-slate-500">
                      {bucketInfo(bucket)?.hint}. Showing this kind only — reorder from All.
                    </p>
                  )}
                </div>
              )}
              {reorderError && (
                <p className="text-xs text-red-700 bg-red-50 border-b border-red-100 px-3 py-2">
                  {reorderError}
                </p>
              )}
              <div key={bucket} data-question-list className="relative max-h-[70vh] overflow-y-auto dvv-cascade">
                {visible.map((q) => (
                  <QuestionRow
                    key={q.qid}
                    q={q}
                    entry={map[q.qid]}
                    selected={q.qid === selectedQid}
                    onClick={() => setSelectedQid(q.qid)}
                    draggable={canWrite && bucket === 'all'}
                    dragging={dragQid === q.qid}
                    dropBefore={dropBeforeQid === q.qid && dragQid !== q.qid}
                    onDragStart={() => setDragQid(q.qid)}
                    onDragOver={(e) => {
                      if (!dragQid) return
                      e.preventDefault()
                      setDropBeforeQid(q.qid)
                    }}
                    onDrop={() => {
                      if (dragQid && dragQid !== q.qid) {
                        moveQid(dragQid, questions.findIndex((x) => x.qid === q.qid))
                      }
                      setDragQid(null)
                      setDropBeforeQid(null)
                    }}
                    onDragEnd={() => {
                      setDragQid(null)
                      setDropBeforeQid(null)
                    }}
                    onMoveKey={(delta) =>
                      moveQid(q.qid, questions.findIndex((x) => x.qid === q.qid) + delta)
                    }
                  />
                ))}
                {questions.length === 0 && (
                  <p className="text-sm text-slate-400 p-4">No questions extracted.</p>
                )}
              </div>
            </div>

            {/* Detail */}
            <div className="col-span-3 bg-white rounded-xl border border-slate-200 shadow-sm p-6">
              {selected ? (
                <div key={selected.qid} className="dvv-rise">
                <ConnectionsPanel
                  studyId={studyId!}
                  question={selected}
                  entry={map[selected.qid]}
                  earlier={questions.slice(0, questions.findIndex((q) => q.qid === selected.qid))}
                  canWrite={canWrite}
                  onChanged={onQuestionSaved}
                  onGoTo={goTo}
                />
                <QuestionDetail
                  key={selected.qid}
                  studyId={studyId!}
                  q={selected}
                  priorQids={questions.map((qq) => qq.qid)}
                  canWrite={canWrite}
                  canCorrect={canCorrect}
                  profile={status?.profile}
                  onSaved={onQuestionSaved}
                  onApproved={onQuestionApproved}
                  onSplit={onQuestionSplit}
                />
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-16 text-center">
                  <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-50 text-2xl text-indigo-400">
                    ⇄
                  </div>
                  <p className="text-sm font-medium text-slate-600">{tEmpty}</p>
                  <p className="mt-1 max-w-xs text-xs text-slate-400">
                    Each question shows what decides who sees it, where its options come from, and what its
                    answer decides later.
                  </p>
                </div>
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
