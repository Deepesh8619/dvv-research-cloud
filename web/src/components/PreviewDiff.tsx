import { useCallback, useEffect, useRef, useState } from 'react'
import {
  previewApi,
  type CorrectionResult,
  type ExtractionDiff,
  type PreviewState,
  type QuestionDiff,
} from '../api/client'

/**
 * The before/after screen (P4).
 *
 * Design rule that drives the whole layout: REGRESSIONS COME FIRST and cannot
 * be collapsed away. A rule that fixes 41 questions and quietly breaks 6 is not
 * a success, and a review UI that leads with the wins would let exactly that
 * through. The counts row puts "regressed" beside "improved" at equal weight,
 * and the list is server-sorted worst-first.
 *
 * "changed" is deliberately its own neutral category — both sides had a value
 * and they differ, which no heuristic can call. It is never scored as a win.
 */

const VERDICT_STYLE: Record<string, string> = {
  improved: 'text-emerald-700 bg-emerald-50 border-emerald-200',
  regressed: 'text-rose-700 bg-rose-50 border-rose-200',
  changed: 'text-amber-700 bg-amber-50 border-amber-200',
  unchanged: 'text-slate-500 bg-slate-50 border-slate-200',
}
const VERDICT_BAR: Record<string, string> = {
  improved: 'bg-emerald-500',
  regressed: 'bg-rose-500',
  changed: 'bg-amber-400',
  unchanged: 'bg-slate-200',
}
const VERDICT_WORD: Record<string, string> = {
  regressed: 'Worse',
  changed: 'Changed',
  improved: 'Better',
  unchanged: 'Unchanged',
}
const FIELD_WORD: Record<string, string> = {
  answers: 'answers',
  text: 'wording',
  type: 'type',
  show_if: 'who is asked',
  terminate_if: 'screen-out',
}

type Verdict = 'improved' | 'regressed' | 'changed' | 'unchanged'
type Filter = Verdict | 'all'

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '— none —'
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    if (value.length === 0) return '— none —'
    return value
      .map((v) =>
        v && typeof v === 'object' && 'label' in (v as Record<string, unknown>)
          ? `${(v as { code?: unknown }).code}. ${(v as { label?: unknown }).label}`
          : typeof v === 'string' ? v : JSON.stringify(v),
      )
      .join(' · ')
  }
  return JSON.stringify(value)
}

/** Whole-questionnaire accuracy: the same two measures on both sides. */
function QualityBars({ diff }: { diff: ExtractionDiff }) {
  const q = diff.quality
  if (!q) return null
  const rows: { label: string; hint: string; before: number | null; after: number | null }[] = [
    {
      label: 'Passes QA',
      hint: 'questions the QA agent finds nothing wrong with',
      before: q.before.qa_pass_pct,
      after: q.after.qa_pass_pct,
    },
    {
      label: 'Can be answered',
      hint: 'questions a respondent has something to answer on',
      before: q.before.answerable_pct,
      after: q.after.answerable_pct,
    },
  ]
  return (
    <div className="border border-slate-200 rounded-md bg-white p-4 mb-3 space-y-3">
      {rows.map((r) => {
        const delta = r.before !== null && r.after !== null ? r.after - r.before : null
        return (
          <div key={r.label}>
            <div className="flex items-baseline justify-between gap-2 mb-1">
              <span className="text-sm font-medium text-slate-800">
                {r.label} <span className="font-normal text-xs text-slate-500">— {r.hint}</span>
              </span>
              <span className="text-sm font-mono tabular-nums whitespace-nowrap">
                <span className="text-slate-400">{r.before ?? '—'}%</span>
                <span className="text-slate-400"> → </span>
                <span className="font-semibold text-slate-900">{r.after ?? '—'}%</span>
                {delta !== null && delta !== 0 && (
                  <span className={delta > 0 ? 'text-emerald-700' : 'text-rose-700'}>
                    {' '}({delta > 0 ? '+' : ''}{delta})
                  </span>
                )}
              </span>
            </div>
            <div className="space-y-1" aria-hidden>
              <div className="h-1.5 rounded bg-slate-100">
                <div className="h-1.5 rounded bg-slate-300" style={{ width: `${r.before ?? 0}%` }} />
              </div>
              <div className="h-2 rounded bg-slate-100">
                <div
                  className={`h-2 rounded ${delta !== null && delta < 0 ? 'bg-rose-500' : 'bg-emerald-500'}`}
                  style={{ width: `${r.after ?? 0}%` }}
                />
              </div>
            </div>
          </div>
        )
      })}
      <p className="text-xs text-slate-500">
        Before is this study as it is stored. After is a fresh read with this rule — so it also
        includes any reader improvements made since the study was first read.
      </p>
    </div>
  )
}

/** One bar for the whole split; its legend doubles as the filter. */
function VerdictBar({
  diff,
  filter,
  onFilter,
}: {
  diff: ExtractionDiff
  filter: Filter
  onFilter: (f: Filter) => void
}) {
  const order: Verdict[] = ['regressed', 'changed', 'improved', 'unchanged']
  const total = order.reduce((n, v) => n + diff.counts[v], 0) || 1
  return (
    <div className="mb-3">
      <div className="flex h-3 rounded overflow-hidden bg-slate-100" aria-hidden>
        {order.map((v) =>
          diff.counts[v] ? (
            <div key={v} className={VERDICT_BAR[v]} style={{ width: `${(100 * diff.counts[v]) / total}%` }} />
          ) : null,
        )}
      </div>
      <div className="flex flex-wrap gap-1.5 mt-2">
        {order
          .filter((v) => v !== 'unchanged')
          .map((v) => (
            <button
              key={v}
              onClick={() => onFilter(v)}
              disabled={!diff.counts[v]}
              className={`text-xs px-2.5 py-1 rounded-full border transition disabled:opacity-40 ${
                filter === v ? VERDICT_STYLE[v] + ' font-semibold' : 'border-slate-200 text-slate-600'
              }`}
            >
              {VERDICT_WORD[v]} {diff.counts[v]}
            </button>
          ))}
        <button
          onClick={() => onFilter('all')}
          className={`text-xs px-2.5 py-1 rounded-full border ${
            filter === 'all' ? 'border-slate-400 font-semibold' : 'border-slate-200 text-slate-600'
          }`}
        >
          All {diff.questions.length}
        </button>
        <span className="text-xs text-slate-400 self-center ml-auto">
          {diff.counts.unchanged} unchanged · {diff.n_questions} re-read
        </span>
      </div>
    </div>
  )
}

/** Answers as what was added and removed — not two copies of a long list. */
function AnswersChange({ before, after }: { before: unknown; after: unknown }) {
  const b = (before ?? {}) as Record<string, string[]>
  const a = (after ?? {}) as Record<string, string[]>
  const keys = Array.from(new Set([...Object.keys(b), ...Object.keys(a)]))
  const clean = (x: string) => x.replace(/\[[^\]]*\]/g, ' ').replace(/^[\s|]+|[\s|]+$/g, '').replace(/\s+/g, ' ')
  return (
    <div className="space-y-2">
      {keys.map((k) => {
        const was = b[k] ?? []
        const now = a[k] ?? []
        const removed = was.filter((x) => !now.includes(x))
        const added = now.filter((x) => !was.includes(x))
        const cleanedOnly =
          removed.length > 0 && removed.map(clean).join('\u0000') === added.map(clean).join('\u0000')
        return (
          <div key={k} className="text-sm">
            <span className="text-[10px] font-mono uppercase tracking-wide text-slate-400 mr-2">
              {k} {was.length}→{now.length}
            </span>
            {cleanedOnly ? (
              <span className="text-emerald-700">
                same {now.length} answers — scripter notes and “|” separators removed
              </span>
            ) : (
              <div className="flex flex-wrap gap-1 mt-1">
                {removed.map((x) => (
                  <span key={'-' + x} className="px-1.5 py-0.5 rounded bg-rose-50 text-rose-700 line-through break-all">
                    {x}
                  </span>
                ))}
                {added.map((x) => (
                  <span key={'+' + x} className="px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 break-all">
                    {x}
                  </span>
                ))}
                {!removed.length && !added.length && <span className="text-slate-500">order changed</span>}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function ChangeRow({ q }: { q: QuestionDiff }) {
  const [open, setOpen] = useState(false)
  const fields = q.changes.map((c) => FIELD_WORD[c.field] ?? c.field)
  const summary =
    q.only_in === 'before'
      ? 'no longer read from the document'
      : q.only_in === 'after'
        ? 'newly recovered from the document'
        : fields.length
          ? fields.join(' · ')
          : q.renumbered_from
            ? 'renumbered only'
            : 'flags only'
  return (
    <div className="border-t border-slate-200 first:border-t-0">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 py-2 px-1 text-left hover:bg-slate-50 min-w-0"
      >
        <span className="font-mono text-sm font-semibold text-slate-800 w-14 shrink-0">{q.qid}</span>
        <span
          className={`text-[10px] font-mono uppercase tracking-wide px-1.5 py-0.5 rounded border shrink-0 ${VERDICT_STYLE[q.verdict]}`}
        >
          {VERDICT_WORD[q.verdict]}
        </span>
        {q.renumbered_from && (
          <span className="text-xs text-slate-500 shrink-0">was {q.renumbered_from}</span>
        )}
        <span className="text-sm text-slate-600 truncate min-w-0">{summary}</span>
        <span className="ml-auto text-slate-400 text-xs shrink-0">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="pl-16 pr-1 pb-3 space-y-3 min-w-0">
          {q.changes.map((c) => (
            <div key={c.field} className="min-w-0">
              <div className="text-[10px] font-mono uppercase tracking-wide text-slate-400 mb-1">
                {FIELD_WORD[c.field] ?? c.field}
              </div>
              {c.field === 'answers' ? (
                <AnswersChange before={c.before} after={c.after} />
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
                  <div className="text-slate-500 break-words min-w-0">
                    <span className="text-[10px] uppercase text-slate-400 mr-1">before</span>
                    {fmt(c.before)}
                  </div>
                  <div className="text-slate-900 break-words min-w-0">
                    <span className="text-[10px] uppercase text-slate-400 mr-1">after</span>
                    {fmt(c.after)}
                  </div>
                </div>
              )}
            </div>
          ))}
          {(q.flags_cleared.length > 0 || q.flags_added.length > 0) && (
            <div className="flex flex-wrap gap-1.5">
              {q.flags_cleared.map((f) => (
                <span key={f} className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700">
                  resolved: {f}
                </span>
              ))}
              {q.flags_added.map((f) => (
                <span key={f} className="text-[10px] px-1.5 py-0.5 rounded bg-rose-50 text-rose-700">
                  new: {f}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** The change list as a spreadsheet — one row per field that moved. */
function downloadCsv(diff: ExtractionDiff) {
  const cell = (v: unknown) => `"${fmt(v).replace(/"/g, '""')}"`
  const lines = [['question', 'was', 'verdict', 'what', 'before', 'after'].join(',')]
  for (const q of diff.questions) {
    const rows = q.changes.length
      ? q.changes.map((c) => [c.field, c.before, c.after])
      : [[q.only_in ? `only in ${q.only_in}` : 'flags', '', '']]
    for (const [field, before, after] of rows) {
      lines.push(
        [q.qid, q.renumbered_from ?? '', VERDICT_WORD[q.verdict], field, cell(before), cell(after)].join(','),
      )
    }
  }
  const url = URL.createObjectURL(new Blob(['﻿' + lines.join('\n')], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  a.download = 'rule-preview-changes.csv'
  a.click()
  URL.revokeObjectURL(url)
}

export function ReadyView({ diff }: { diff: ExtractionDiff }) {
  const initial: Filter = diff.counts.regressed ? 'regressed' : diff.counts.changed ? 'changed' : 'all'
  const [filter, setFilter] = useState<Filter>(initial)
  const shown = filter === 'all' ? diff.questions : diff.questions.filter((q) => q.verdict === filter)
  return (
    <>
      <QualityBars diff={diff} />
      <VerdictBar diff={diff} filter={filter} onFilter={setFilter} />
      {diff.counts.regressed > 0 && (
        <p className="text-sm text-rose-800 bg-rose-50 border border-rose-200 rounded p-3 mb-3">
          {diff.counts.regressed} question{diff.counts.regressed === 1 ? '' : 's'} got worse under
          this rule. Open each one before approving.
        </p>
      )}
      {diff.questions.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing changed. This rule had no effect on the document.</p>
      ) : (
        <div className="border border-slate-200 rounded-md bg-white max-h-[22rem] overflow-y-auto overflow-x-hidden px-2">
          {shown.map((q) => (
            <ChangeRow key={`${q.qid}-${q.renumbered_from ?? ''}-${q.only_in ?? ''}`} q={q} />
          ))}
        </div>
      )}
      {diff.questions.length > 0 && (
        <button
          onClick={() => downloadCsv(diff)}
          className="mt-2 text-xs text-slate-600 underline hover:text-slate-900"
        >
          Download the changes (CSV, opens in Excel)
        </button>
      )}
    </>
  )
}

interface Props {
  studyId: string
  correctionId: string
  onApplied?: () => void
  /** Called after a send-back, with the model's fresh attempt. */
  onRevised?: (result: CorrectionResult) => void
}

export default function PreviewDiff({ studyId, correctionId, onApplied, onRevised }: Props) {
  const [state, setState] = useState<PreviewState | null>(null)
  const [starting, setStarting] = useState(false)
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [estimate, setEstimate] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [sendingBack, setSendingBack] = useState(false)
  const timer = useRef<number | null>(null)

  const poll = useCallback(async () => {
    try {
      const r = await previewApi.get(studyId, correctionId)
      setState(r.data)
      if (r.data.status === 'running') {
        timer.current = window.setTimeout(poll, 3000)
      }
    } catch {
      // 404 simply means no preview has been started yet.
    }
  }, [studyId, correctionId])

  useEffect(() => {
    poll()
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [poll])

  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      const r = await previewApi.start(studyId, correctionId)
      setEstimate(r.data.estimated_model_calls ?? null)
      poll()
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Could not start the preview.')
    } finally {
      setStarting(false)
    }
  }

  const sendBack = async () => {
    if (!note.trim()) return
    setSendingBack(true)
    setError(null)
    try {
      const r = await previewApi.revise(studyId, correctionId, note.trim())
      setNote('')
      setState(null)
      onRevised?.(r.data)
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Could not send it back.')
    } finally {
      setSendingBack(false)
    }
  }

  const approve = async () => {
    setApproving(true)
    setError(null)
    try {
      await previewApi.approve(studyId, correctionId)
      onApplied?.()
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Could not apply this rule.')
    } finally {
      setApproving(false)
    }
  }

  if (!state && !starting && !error) {
    return (
      <div className="mt-3">
        <button
          onClick={start}
          className="text-sm font-medium bg-slate-900 text-white px-4 py-1.5 rounded
                     hover:bg-slate-700 active:scale-[0.98] transition
                     focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
        >
          See what this changes
        </button>
        <p className="text-xs text-slate-500 mt-1.5">
          Re-reads the whole document under this rule. Costs one model call per question,
          and changes nothing until you approve it.
        </p>
      </div>
    )
  }

  return (
    <div className="mt-3">
      {error && (
        <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-3 mb-3">
          {error}
        </div>
      )}

      {state?.status === 'running' && (
        <div className="text-sm text-slate-600 flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-sky-500 animate-pulse" />
          Re-reading the document
          {estimate ? ` (${estimate} questions)` : ''}… this takes a few minutes.
        </div>
      )}

      {state?.status === 'error' && (
        <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-3">
          Re-reading failed: {state.error_detail}
        </div>
      )}

      {state?.status === 'ready' && state.diff && (
        <>
          <ReadyView diff={state.diff} />

          <div className="flex gap-2 mt-4 pt-3 border-t border-slate-200">
            <button
              onClick={approve}
              disabled={approving}
              className="text-sm font-medium bg-emerald-700 text-white px-4 py-1.5 rounded
                         hover:bg-emerald-800 disabled:opacity-40 active:scale-[0.98] transition
                         focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
            >
              {approving ? 'Applying…' : 'Approve & teach the parser'}
            </button>
            <button
              onClick={start}
              className="text-sm text-slate-600 px-3 py-1.5 rounded border border-slate-300
                         hover:bg-slate-50 active:scale-[0.98] transition"
            >
              Re-run
            </button>
          </div>

          {/* Send back: the third spec'd review action. Without it a reviewer
              whose rule broke Q31 could only rewrite from scratch, discarding
              everything the model already understood. */}
          <div className="mt-3 pt-3 border-t border-slate-200">
            <label
              htmlFor="revise-note"
              className="block text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1.5"
            >
              Not right? Say what's wrong and send it back
            </label>
            <textarea
              id="revise-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              placeholder="e.g. this broke Q31 — that question uses a different row label"
              className="w-full text-sm rounded border border-slate-300 px-3 py-2 bg-white
                         focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-sky-500
                         placeholder:text-slate-400"
            />
            <button
              onClick={sendBack}
              disabled={sendingBack || !note.trim()}
              className="mt-2 text-sm text-slate-700 px-3 py-1.5 rounded border border-slate-300
                         hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed
                         active:scale-[0.98] transition
                         focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
            >
              {sendingBack ? 'Sending back…' : 'Send back with this note'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
