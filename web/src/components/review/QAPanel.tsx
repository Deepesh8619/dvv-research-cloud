import { useState } from 'react'
import type { QARecord } from '../../api/client'

/**
 * The QA loop's record: the QA agent tested every question as a respondent
 * meets it and against the document, the programmer agent fixed what it safely
 * could, and what is left needs a person.
 */
export default function QAPanel({
  qa,
  canRun,
  onRun,
  onGoTo,
}: {
  qa: QARecord | null | undefined
  canRun: boolean
  onRun: () => Promise<void>
  onGoTo: (qid: string) => void
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
      </div>
    </details>
  )
}
