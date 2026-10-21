import { useEffect, useState } from 'react'

import { questionnaireApi, type StudyQuote } from '../api/client'

/**
 * A quote read from the questionnaire, before anyone scripts it: how long the
 * interview will take, which questions decide who qualifies, how many people
 * must start, and — from your own rates — what the sample costs.
 */
const field = 'w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm tabular-nums'

function num(v: string): number | undefined {
  const n = Number(v)
  return v.trim() === '' || Number.isNaN(n) ? undefined : n
}

export default function QuotePanel({ studyId }: { studyId: string }) {
  const [form, setForm] = useState({ completes: '', incidence: '', rate: '', minCpi: '', fee: '' })
  const [quote, setQuote] = useState<StudyQuote | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const handle = window.setTimeout(() => {
      questionnaireApi
        .quote(studyId, {
          completes: num(form.completes), incidence_pct: num(form.incidence),
          rate_per_minute: num(form.rate), min_cpi: num(form.minCpi), project_fee: num(form.fee),
        })
        .then((res) => { if (!cancelled) { setQuote(res.data); setError(null) } })
        .catch(() => { if (!cancelled) setError('A quote needs an extracted questionnaire.') })
    }, 300)
    return () => { cancelled = true; window.clearTimeout(handle) }
  }, [studyId, form])

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value })
  const L = quote?.length
  const money = (v?: number) => (v === undefined ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: 2 }))

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-panel" data-testid="quote-panel">
      <h2 className="font-semibold text-slate-900">Quote from the questionnaire</h2>
      <p className="mt-1 max-w-2xl text-sm text-slate-500">
        Read from the document before anyone scripts it. Length is a range (±30%: its measured error on real studies);
        cost uses only the rates you enter.
      </p>
      {error && <p className="mt-3 text-sm text-slate-500">{error}</p>}
      {quote && L && (
        <div className="mt-4 grid gap-5 lg:grid-cols-2">
          <div className="space-y-3">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Interview length</p>
              <p className="text-2xl font-semibold tabular-nums text-slate-900">{L.low}–{L.high} min</p>
              <p className="text-xs text-slate-500">
                best estimate {L.minutes} min · {quote.n_questions} questions
                {L.stated_minutes ? ` · the document promises ${L.stated_minutes} min` : ''}
              </p>
              {L.stated_minutes && L.stated_minutes < L.low && (
                <p className="mt-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  The document's {L.stated_minutes} minutes is below the low end of the estimate; a study priced on it is likely under-priced.
                </p>
              )}
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Longest questions</p>
              <ul className="mt-1 text-sm text-slate-700">
                {L.longest.map((q) => (
                  <li key={q.qid} className="flex justify-between gap-3"><span className="font-mono text-xs">{q.qid}</span><span className="tabular-nums text-slate-500">{q.seconds}s</span></li>
                ))}
              </ul>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Who qualifies is decided at</p>
              {quote.screeners.length === 0 ? (
                <p className="text-sm text-slate-500">No screen-outs in the document (a recruited sample, or screening done elsewhere).</p>
              ) : (
                <ol className="mt-1 space-y-1 text-sm text-slate-700">
                  {quote.screeners.map((s) => (
                    <li key={s.qid} className="min-w-0"><span className="font-mono text-xs">{s.qid}</span> <span className="text-slate-500">— {s.screens_out}</span></li>
                  ))}
                </ol>
              )}
            </div>
          </div>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs text-slate-600">Completes
                <input className={field} inputMode="numeric" value={form.completes} onChange={set('completes')} placeholder={quote.completes ? String(quote.completes) : 'e.g. 500'} />
              </label>
              <label className="text-xs text-slate-600">Incidence %
                <input className={field} inputMode="decimal" value={form.incidence} onChange={set('incidence')} placeholder="e.g. 30" />
              </label>
              <label className="text-xs text-slate-600">Your rate per minute
                <input className={field} inputMode="decimal" value={form.rate} onChange={set('rate')} placeholder="per complete-minute" />
              </label>
              <label className="text-xs text-slate-600">Minimum cost per complete
                <input className={field} inputMode="decimal" value={form.minCpi} onChange={set('minCpi')} placeholder="optional" />
              </label>
              <label className="col-span-2 text-xs text-slate-600">Project fee
                <input className={field} inputMode="decimal" value={form.fee} onChange={set('fee')} placeholder="programming, DP, reporting — optional" />
              </label>
            </div>
            {quote.cost ? (
              <dl className="grid grid-cols-2 gap-3 rounded-lg bg-slate-50 p-3 text-sm">
                <div><dt className="text-xs text-slate-500">Starts needed</dt><dd className="font-semibold tabular-nums">{quote.cost.starts_needed.toLocaleString()}</dd></div>
                <div><dt className="text-xs text-slate-500">Cost per complete</dt><dd className="font-semibold tabular-nums">{money(quote.cost.cost_per_complete)}</dd></div>
                <div><dt className="text-xs text-slate-500">Sample cost</dt><dd className="font-semibold tabular-nums">{money(quote.cost.sample_cost)}</dd></div>
                <div><dt className="text-xs text-slate-500">Total</dt><dd className="font-semibold tabular-nums">{money(quote.cost.total)}</dd></div>
              </dl>
            ) : (
              <p className="text-sm text-slate-500">Enter completes and incidence to see the starts needed; add your rate to see the cost.</p>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
