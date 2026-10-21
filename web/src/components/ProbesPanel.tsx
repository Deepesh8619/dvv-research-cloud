import { useEffect, useState } from 'react'

import { questionnaireApi } from '../api/client'

/**
 * Which open questions get one follow-up when the answer is thin ("Lower the
 * rates", "Nothing comes to mind"). The follow-up is written for that answer
 * by an open model — neutral, never suggesting an answer — with a fixed one
 * when no model answers. Both the follow-up and its answer land in the data.
 */
type Row = { qid: string; text: string; probed: boolean }

export default function ProbesPanel({ studyId }: { studyId: string }) {
  const [rows, setRows] = useState<Row[] | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    questionnaireApi.probes(studyId).then((r) => setRows(r.data.questions)).catch(() => setRows(null))
  }, [studyId])

  if (!rows || rows.length === 0) return null
  const toggle = (qid: string) => {
    setSaved(null)
    setRows(rows.map((r) => (r.qid === qid ? { ...r, probed: !r.probed } : r)))
  }
  const save = () =>
    questionnaireApi.setProbes(studyId, rows.filter((r) => r.probed).map((r) => r.qid))
      .then((r) => { setRows(r.data.questions); setSaved('Saved. Links opened from now on ask the follow-ups.') })
      .catch(() => setSaved('Could not save.'))

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-panel" data-testid="probes">
      <h2 className="font-semibold text-slate-900">Follow-up probes</h2>
      <p className="mt-1 max-w-2xl text-sm text-slate-500">
        Tick the "why" questions. A respondent whose answer is a stock reply or under six words is asked once more, in
        words written for what they said. Leave job titles and names unticked: short is right there.
      </p>
      <ul className="mt-3 max-h-64 space-y-1 overflow-y-auto text-sm">
        {rows.map((r) => (
          <li key={r.qid}>
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-1" checked={r.probed} onChange={() => toggle(r.qid)} />
              <span><span className="font-mono text-slate-900">{r.qid}</span> <span className="text-slate-600">{r.text}</span></span>
            </label>
          </li>
        ))}
      </ul>
      <button type="button" onClick={save}
        className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white">Save</button>
      {saved && <p className="mt-2 text-sm text-slate-600">{saved}</p>}
    </section>
  )
}
