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

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '— none —'
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    if (value.length === 0) return '— none —'
    return value
      .map((v) =>
        v && typeof v === 'object' && 'label' in (v as Record<string, unknown>)
          ? `${(v as { code?: unknown }).code}. ${(v as { label?: unknown }).label}`
          : JSON.stringify(v),
      )
      .join('  ·  ')
  }
  return JSON.stringify(value)
}

function Counts({ diff }: { diff: ExtractionDiff }) {
  const { counts, n_questions } = diff
  const cell = 'px-4 py-3 border-r border-slate-200 last:border-r-0 flex-1 min-w-[110px]'
  return (
    <div className="flex flex-wrap border border-slate-200 rounded-md bg-white mb-4">
      {/* Regressed sits beside improved at equal visual weight, deliberately. */}
      <div className={cell}>
        <div className="text-2xl font-mono tabular-nums text-emerald-700">
          {counts.improved}
        </div>
        <div className="text-xs text-slate-500 mt-0.5">Improved</div>
      </div>
      <div className={cell}>
        <div className="text-2xl font-mono tabular-nums text-rose-700">
          {counts.regressed}
        </div>
        <div className="text-xs text-slate-500 mt-0.5">Regressed</div>
      </div>
      <div className={cell}>
        <div className="text-2xl font-mono tabular-nums text-amber-700">
          {counts.changed}
        </div>
        <div className="text-xs text-slate-500 mt-0.5">Changed</div>
      </div>
      <div className={cell}>
        <div className="text-2xl font-mono tabular-nums text-slate-400">
          {counts.unchanged}
        </div>
        <div className="text-xs text-slate-500 mt-0.5">Unchanged</div>
      </div>
      <div className={cell}>
        <div className="text-2xl font-mono tabular-nums text-slate-700">{n_questions}</div>
        <div className="text-xs text-slate-500 mt-0.5">Questions re-read</div>
      </div>
    </div>
  )
}

function QuestionRow({ q }: { q: QuestionDiff }) {
  return (
    <div className="border-t border-slate-200 py-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="font-mono text-sm font-semibold text-slate-800">{q.qid}</span>
        <span
          className={`text-[10px] font-mono uppercase tracking-wide px-1.5 py-0.5
                      rounded border ${VERDICT_STYLE[q.verdict]}`}
        >
          {q.verdict}
        </span>
        {q.only_in === 'before' && (
          <span className="text-xs text-rose-700">no longer extracted</span>
        )}
        {q.only_in === 'after' && (
          <span className="text-xs text-emerald-700">newly recovered</span>
        )}
      </div>

      {q.changes.map((c) => (
        <div key={c.field} className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-2 text-sm">
          <div className="min-w-0">
            <div className="text-[10px] font-mono uppercase tracking-wide text-slate-400 mb-0.5">
              {c.field} · before
            </div>
            <div className="text-slate-500 break-words">{fmt(c.before)}</div>
          </div>
          <div className="min-w-0">
            <div className="text-[10px] font-mono uppercase tracking-wide text-slate-400 mb-0.5">
              after
            </div>
            <div
              className={`break-words ${
                c.verdict === 'regressed'
                  ? 'text-rose-700'
                  : c.verdict === 'improved'
                    ? 'text-emerald-700'
                    : 'text-slate-800'
              }`}
            >
              {fmt(c.after)}
            </div>
          </div>
        </div>
      ))}

      {(q.flags_cleared.length > 0 || q.flags_added.length > 0) && (
        <div className="flex flex-wrap gap-1.5 mt-1">
          {q.flags_cleared.map((f) => (
            <span key={f} className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700">
              cleared: {f}
            </span>
          ))}
          {q.flags_added.map((f) => (
            <span key={f} className="text-[10px] px-1.5 py-0.5 rounded bg-rose-50 text-rose-700">
              added: {f}
            </span>
          ))}
        </div>
      )}
    </div>
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
          <Counts diff={state.diff} />

          {state.diff.counts.regressed > 0 && (
            <p className="text-sm text-rose-800 bg-rose-50 border border-rose-200 rounded p-3 mb-3">
              This rule made {state.diff.counts.regressed} question
              {state.diff.counts.regressed === 1 ? '' : 's'} worse. Read those before
              approving — they are listed first.
            </p>
          )}

          {state.diff.questions.length === 0 ? (
            <p className="text-sm text-slate-500">
              Nothing changed. This rule had no effect on the document.
            </p>
          ) : (
            <div className="max-h-[28rem] overflow-y-auto pr-1">
              {state.diff.questions.map((q) => (
                <QuestionRow key={q.qid} q={q} />
              ))}
            </div>
          )}

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
