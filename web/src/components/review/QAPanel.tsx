import { useState } from 'react'
import type { LogicReview, QARecord } from '../../api/client'

/**
 * The QA loop's record: the QA agent tested every question as a respondent
 * meets it and against the document, the programmer agent fixed what it safely
 * could, and what is left needs a person.
 */
/**
 * The AI logic reviewer's second opinion: problems BETWEEN questions that no
 * QA rule covers. Shown for a person to judge — it can be wrong, so it never
 * flags or blocks a question.
 */
function SecondOpinion({
  review,
  canRun,
  onRun,
  onGoTo,
}: {
  review: LogicReview | null | undefined
  canRun: boolean
  onRun?: () => Promise<void>
  onGoTo: (qid: string) => void
}) {
  const [running, setRunning] = useState(false)
  const findings = review?.findings ?? []
  return (
    <section className="border-t border-slate-200 pt-3">
      <h4 className="font-medium text-slate-800">
        Second opinion — AI logic reviewer{' '}
        <span className="font-normal text-xs text-slate-500">
          reads the whole survey for problems between questions · it can be wrong, judge each
        </span>
      </h4>
      {!review && <p className="text-xs text-slate-500 mt-1">Not run on this study yet.</p>}
      {review?.unavailable && (
        <p className="text-xs text-amber-800 mt-1">No AI model could be reached, so the second opinion did not run.</p>
      )}
      {review && !review.unavailable && findings.length === 0 && (
        <p className="text-xs text-emerald-700 mt-1">
          Nothing beyond what QA already reports
          {review.already_reported_by_qa ? ` (it agreed with QA on ${review.already_reported_by_qa})` : ''}.
        </p>
      )}
      <ul className="mt-1 space-y-2">
        {findings.map((f, i) => (
          <li key={i} className="text-sm">
            <span
              className={`text-[10px] uppercase font-mono px-1.5 py-0.5 rounded mr-1.5 ${
                f.severity === 'error' ? 'bg-rose-50 text-rose-700' : 'bg-amber-50 text-amber-700'
              }`}
            >
              {f.severity}
            </span>
            {f.qids.map((qid) => (
              <button key={qid} className="font-mono text-xs underline mr-1" onClick={() => onGoTo(qid)}>
                {qid}
              </button>
            ))}
            {f.problem}
            <q className="block text-xs text-slate-500 italic">{f.evidence}</q>
            {f.fix && <span className="block text-xs text-slate-600">Suggested: {f.fix}</span>}
          </li>
        ))}
      </ul>
      {canRun && onRun && (
        <button
          onClick={async () => {
            setRunning(true)
            try {
              await onRun()
            } finally {
              setRunning(false)
            }
          }}
          disabled={running}
          className="mt-2 text-xs px-3 py-1.5 rounded-md border border-slate-300 hover:bg-slate-50 disabled:opacity-50"
        >
          {running ? 'Reviewing the whole survey…' : review ? 'Ask for a second opinion again' : 'Ask for a second opinion'}
        </button>
      )}
      {review?.model && <p className="text-xs text-slate-400 mt-1">Reviewed by {review.model}.</p>}
    </section>
  )
}

export default function QAPanel({
  qa,
  canRun,
  onRun,
  onGoTo,
  logicReview,
  onRunLogicReview,
}: {
  qa: QARecord | null | undefined
  canRun: boolean
  onRun: () => Promise<void>
  onGoTo: (qid: string) => void
  logicReview?: LogicReview | null
  onRunLogicReview?: () => Promise<void>
}) {
  const [running, setRunning] = useState(false)
  const run = async () => {
    setRunning(true)
    try {
      await onRun()
    } finally {
      setRunning(false)
    }
  }
  const entries = qa?.entries ?? []
  const open = entries.filter((e) => e.status === 'needs_you')
  const fixed = entries.filter((e) => e.status === 'fixed')
  return (
    <details open={open.length > 0} className="bg-white border border-slate-200 rounded-md p-4 mb-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-800">
        QA{' '}
        {qa ? (
          <span className="font-normal text-slate-500">
            — {qa.checked} checked · {qa.fixed} fixed by the programmer agent ·{' '}
            <span className={qa.needs_you ? 'text-amber-700 font-medium' : ''}>
              {qa.needs_you} need{qa.needs_you === 1 ? 's' : ''} you
            </span>
          </span>
        ) : (
          <span className="font-normal text-slate-500">— not run yet</span>
        )}
      </summary>
      <div className="mt-3 space-y-4 text-sm text-slate-700">
        {open.length > 0 && (
          <section>
            <h4 className="font-medium text-amber-800">Needs you</h4>
            <ul className="mt-1 space-y-1.5">
              {open.map((e, i) => (
                <li key={i}>
                  <button className="font-mono text-xs underline mr-1" onClick={() => onGoTo(e.qid)}>
                    {e.qid}
                  </button>
                  {e.detail}
                  {e.evidence && <q className="block text-xs text-slate-500 italic">{e.evidence}</q>}
                </li>
              ))}
            </ul>
          </section>
        )}
        {fixed.length > 0 && (
          <section>
            <h4 className="font-medium text-emerald-800">Fixed and re-checked</h4>
            <ul className="mt-1 space-y-1">
              {fixed.map((e, i) => (
                <li key={i}>
                  <button className="font-mono text-xs underline mr-1" onClick={() => onGoTo(e.qid)}>
                    {e.qid}
                  </button>
                  {e.detail}
                </li>
              ))}
            </ul>
          </section>
        )}
        {qa && entries.length === 0 && <p>Every question passed QA.</p>}
        {canRun && (
          <button
            onClick={run}
            disabled={running}
            className="text-xs px-3 py-1.5 rounded-md border border-slate-300 hover:bg-slate-50 disabled:opacity-50"
          >
            {running ? 'Running QA…' : qa ? 'Run QA again' : 'Run QA'}
          </button>
        )}
        {qa && <p className="text-xs text-slate-400">Last run {qa.ran_at}. Questions you approved are never changed.</p>}
        <SecondOpinion review={logicReview} canRun={canRun} onRun={onRunLogicReview} onGoTo={onGoTo} />
      </div>
    </details>
  )
}
