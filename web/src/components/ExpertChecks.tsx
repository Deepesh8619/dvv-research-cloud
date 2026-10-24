import { useEffect, useState } from 'react'

import { questionnaireApi, type ExpertNotes } from '../api/client'
import InfoTip from './ui/InfoTip'

/**
 * A senior researcher reading over the shoulder while the survey is built:
 * the interview length, and notes on leading wording, two questions in one,
 * missing "None of these", lopsided scales, fixed-order lists and long grids.
 * Advisory — nothing here stops a save.
 */
export default function ExpertChecks({ studyId, version, onGoTo }: {
  studyId: string
  /** Changes whenever the questionnaire does, so the notes follow the edits. */
  version: string
  onGoTo: (qid: string) => void
}) {
  const [notes, setNotes] = useState<ExpertNotes | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const t = window.setTimeout(() => {
      questionnaireApi.expertChecks(studyId).then((r) => setNotes(r.data)).catch(() => setNotes(null))
    }, 400)
    return () => window.clearTimeout(t)
  }, [studyId, version])

  if (!notes) return null
  const total = notes.n_fix + notes.n_consider
  const minutes = `≈ ${Math.round(notes.length.low)}–${Math.round(notes.length.high)} min`
  return (
    <div className="relative flex items-center">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className={`rounded-full border px-3 py-1 text-xs font-medium tabular-nums ${
          notes.n_fix ? 'border-amber-300 bg-amber-50 text-amber-800' : 'border-slate-300 text-slate-600'}`}>
        {minutes} · {total === 0 ? 'no notes' : `${total} note${total === 1 ? '' : 's'}`}
      </button>
      <InfoTip id="expert_notes" />
      {open && (
        <div className="absolute left-0 top-9 z-20 w-[28rem] max-w-[90vw] rounded-lg border border-slate-200 bg-white p-4 shadow-lg">
          <p className="text-sm text-slate-700">
            About {notes.length.minutes.toFixed(0)} minutes for someone asked every question
            ({minutes.replace('≈ ', '')}, the estimate’s range).
          </p>
          {total === 0 ? (
            <p className="mt-2 text-sm text-emerald-700">Nothing a reviewer would flag.</p>
          ) : (
            <ul className="mt-3 max-h-80 space-y-2 overflow-y-auto text-sm">
              {notes.notes.map((n, i) => (
                <li key={i} className="flex gap-2">
                  <span className={`mt-0.5 h-2 w-2 shrink-0 rounded-full ${n.weight === 'fix' ? 'bg-amber-500' : 'bg-slate-300'}`} aria-hidden />
                  <span className="text-slate-700">
                    {n.qid && (
                      <button type="button" className="mr-1 font-mono text-primary-600 hover:underline"
                        onClick={() => { onGoTo(n.qid!); setOpen(false) }}>{n.qid}</button>
                    )}
                    {n.note}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
