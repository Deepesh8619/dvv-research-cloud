import { useEffect, useState } from 'react'

import { complianceApi, type AdverseEventHit, type AdverseEventScan } from '../api/client'
import InfoTip from './ui/InfoTip'

/**
 * Open answers describing a side effect. "Possible" means someone had it — a
 * patient, the respondent — and goes to the drug maker's safety team within a
 * working day; a "mention" names an event nobody said they had. Nothing is
 * reported or dismissed here: a person reads each one.
 */
function Hit({ h }: { h: AdverseEventHit }) {
  return (
    <li className="rounded-md border border-slate-200 p-3 text-sm">
      <p className="text-xs text-slate-500">
        Respondent <span className="font-mono">{h.respondent}</span> · {h.column}
        {h.question ? ` — ${h.question}` : ''}
      </p>
      <p className="mt-1 text-slate-900">“{h.sentence}”</p>
      <p className="mt-1 text-xs text-slate-500">words: {h.terms.join(', ')}</p>
    </li>
  )
}

export default function AdverseEventsPanel({ studyId }: { studyId: string }) {
  const [scan, setScan] = useState<AdverseEventScan | null>(null)

  useEffect(() => {
    complianceApi.adverseEvents(studyId).then((r) => setScan(r.data)).catch(() => setScan(null))
  }, [studyId])

  if (!scan || scan.n_open_ends === 0) return null
  const none = scan.possible.length === 0 && scan.mentions.length === 0

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-panel" data-testid="adverse-events">
      <h2 className="font-semibold text-slate-900">Adverse events in open answers<InfoTip id="adverse_events" /></h2>
      <p className="mt-1 max-w-2xl text-sm text-slate-500">
        {scan.n_open_ends} open questions read across all {scan.n_respondents} interviews, screen-outs and partials
        included.
      </p>
      {none && <p className="mt-3 text-sm text-slate-600">No answer names a side effect.</p>}
      {scan.possible.length > 0 && (
        <div className="mt-4">
          <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
            {scan.possible.length} possible adverse event{scan.possible.length === 1 ? '' : 's'}: someone had it.
            Read each one; report the real ones to the drug maker's safety team within one working day.
          </p>
          <ul className="mt-3 space-y-2">{scan.possible.map((h) => <Hit key={h.respondent + h.column} h={h} />)}</ul>
        </div>
      )}
      {scan.mentions.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-sm text-slate-600">
            {scan.mentions.length} mention{scan.mentions.length === 1 ? '' : 's'} with nobody said to have had it
          </summary>
          <ul className="mt-3 space-y-2">{scan.mentions.map((h) => <Hit key={h.respondent + h.column} h={h} />)}</ul>
        </details>
      )}
    </section>
  )
}
