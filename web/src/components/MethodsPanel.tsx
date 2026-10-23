import { useEffect, useState } from 'react'

import { studiesApi, type MethodInputs, type MethodRequest } from '../api/client'

/**
 * MaxDiff scores, TURF reach, Van Westendorp price range and key drivers,
 * computed from the delivered data (completes only) with no model involved.
 */
type Method = MethodRequest['method']
const NAMES: Record<Method, string> = {
  maxdiff: 'MaxDiff', turf: 'TURF', van_westendorp: 'Van Westendorp', drivers: 'Key drivers',
}
const select = 'w-full rounded-md border border-slate-300 px-2 py-1 text-sm'

function Bar({ pct }: { pct: number }) {
  return (
    <span className="inline-block h-2 w-24 rounded bg-slate-100 align-middle">
      <span className="block h-2 rounded bg-primary-500" style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
    </span>
  )
}

export default function MethodsPanel({ studyId }: { studyId: string }) {
  const [inputs, setInputs] = useState<MethodInputs | null>(null)
  const [method, setMethod] = useState<Method>('maxdiff')
  const [pick, setPick] = useState<{ one: string; size: number; prices: string[]; preds: string[] }>(
    { one: '', size: 5, prices: ['', '', '', ''], preds: [] })
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    studiesApi.methodInputs(studyId).then((r) => setInputs(r.data)).catch(() => setInputs(null))
  }, [studyId])
  if (!inputs) return null

  const available: Record<Method, boolean> = {
    maxdiff: inputs.maxdiff.length > 0, turf: inputs.multi_select.length > 0,
    van_westendorp: inputs.numeric.length >= 4, drivers: inputs.ratings.length >= 3,
  }
  const choose = (m: Method) => { setMethod(m); setResult(null); setError(null); setPick({ ...pick, one: '', preds: [] }) }
  const run = () => {
    const body: MethodRequest =
      method === 'maxdiff' ? { method, exercise: pick.one }
        : method === 'turf' ? { method, question: pick.one, max_size: pick.size }
          : method === 'van_westendorp' ? { method, prices: pick.prices }
            : { method, outcome: pick.one, predictors: pick.preds }
    setError(null)
    studiesApi.runMethod(studyId, body).then((r) => setResult(r.data))
      .catch((e) => { setResult(null); setError(e?.response?.data?.detail ?? 'Could not run this.') })
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-panel" data-testid="methods">
      <h2 className="font-semibold text-slate-900">Advanced methods</h2>
      <p className="mt-1 text-sm text-slate-500">On the {inputs.n_respondents} completes, computed from the data file itself.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {(Object.keys(NAMES) as Method[]).map((m) => (
          <button key={m} type="button" disabled={!available[m]} onClick={() => choose(m)}
            className={`rounded-md border px-3 py-1 text-sm disabled:opacity-40 ${method === m ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300'}`}>
            {NAMES[m]}
          </button>
        ))}
      </div>

      <div className="mt-4 grid max-w-2xl gap-2">
        {method === 'maxdiff' && (
          <label className="text-xs text-slate-600">Exercise
            <select className={select} value={pick.one} onChange={(e) => setPick({ ...pick, one: e.target.value })}>
              <option value="">Choose…</option>
              {inputs.maxdiff.map((x) => <option key={x.exercise} value={x.exercise}>{x.exercise} ({x.screens} screens)</option>)}
            </select>
          </label>
        )}
        {method === 'turf' && (
          <>
            <label className="text-xs text-slate-600">Multi-select question
              <select className={select} value={pick.one} onChange={(e) => setPick({ ...pick, one: e.target.value })}>
                <option value="">Choose…</option>
                {inputs.multi_select.map((x) => <option key={x.question} value={x.question}>{x.question} — {x.text}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-600">Largest set
              <input type="number" min={1} max={8} className={select} value={pick.size}
                onChange={(e) => setPick({ ...pick, size: Number(e.target.value) || 1 })} />
            </label>
          </>
        )}
        {method === 'van_westendorp' && ['Too cheap', 'Cheap (a bargain)', 'Expensive', 'Too expensive'].map((name, i) => (
          <label key={name} className="text-xs text-slate-600">{name}
            <select className={select} value={pick.prices[i]}
              onChange={(e) => setPick({ ...pick, prices: pick.prices.map((p, j) => (j === i ? e.target.value : p)) })}>
              <option value="">Choose…</option>
              {inputs.numeric.map((x) => <option key={x.variable} value={x.variable}>{x.variable} — {x.label}</option>)}
            </select>
          </label>
        ))}
        {method === 'drivers' && (
          <>
            <label className="text-xs text-slate-600">Outcome (overall rating)
              <select className={select} value={pick.one} onChange={(e) => setPick({ ...pick, one: e.target.value })}>
                <option value="">Choose…</option>
                {inputs.ratings.map((x) => <option key={x.variable} value={x.variable}>{x.variable} — {x.label}</option>)}
              </select>
            </label>
            <fieldset className="max-h-48 overflow-y-auto rounded-md border border-slate-200 p-2 text-sm">
              <legend className="px-1 text-xs text-slate-600">Attributes</legend>
              {inputs.ratings.filter((x) => x.variable !== pick.one).map((x) => (
                <label key={x.variable} className="flex items-start gap-2">
                  <input type="checkbox" className="mt-1" checked={pick.preds.includes(x.variable)}
                    onChange={() => setPick({ ...pick, preds: pick.preds.includes(x.variable)
                      ? pick.preds.filter((p) => p !== x.variable) : [...pick.preds, x.variable] })} />
                  <span><span className="font-mono">{x.variable}</span> {x.label}</span>
                </label>
              ))}
            </fieldset>
          </>
        )}
        <button type="button" onClick={run} className="w-fit rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white">Run</button>
        {error && <p className="text-sm text-red-700">{error}</p>}
      </div>

      {result && method === 'maxdiff' && (
        <table className="mt-4 w-full max-w-3xl text-sm tabular-nums">
          <caption className="mb-2 text-left text-xs text-slate-500">{result.n_respondents} respondents · score = (best − worst) {result.basis}</caption>
          <thead className="text-left text-[11px] uppercase tracking-wider text-slate-500"><tr><th>Item</th><th>Best</th><th>Worst</th><th>Score</th><th /></tr></thead>
          <tbody>{result.items.map((r: { item: number; label: string; best: number; worst: number; score: number | null }) => (
            <tr key={r.item} className="border-t border-slate-100"><td className="py-1">{r.label}</td><td>{r.best}</td><td>{r.worst}</td>
              <td>{r.score}</td><td><Bar pct={50 + (r.score ?? 0) * 50} /></td></tr>))}</tbody>
        </table>
      )}
      {result && method === 'turf' && (
        <table className="mt-4 w-full max-w-3xl text-sm tabular-nums">
          <caption className="mb-2 text-left text-xs text-slate-500">{result.n_respondents} respondents · reach = chose at least one of the set</caption>
          <thead className="text-left text-[11px] uppercase tracking-wider text-slate-500"><tr><th>Set of</th><th>Options</th><th>Reach</th><th /></tr></thead>
          <tbody>{result.sets.map((s: { size: number; options: string[]; reach_pct: number; searched: string }) => (
            <tr key={s.size} className="border-t border-slate-100 align-top"><td className="py-1">{s.size}</td><td>{s.options.join(', ')}</td>
              <td>{s.reach_pct}%</td><td><Bar pct={s.reach_pct} /></td></tr>))}</tbody>
        </table>
      )}
      {result && method === 'van_westendorp' && (
        <dl className="mt-4 grid max-w-xl grid-cols-2 gap-2 text-sm">
          <dt className="text-slate-500">Acceptable range</dt>
          <dd>{result.points.point_of_marginal_cheapness ?? '—'} to {result.points.point_of_marginal_expensiveness ?? '—'}</dd>
          <dt className="text-slate-500">Optimal price point</dt><dd>{result.points.optimal_price_point ?? '—'}</dd>
          <dt className="text-slate-500">Indifference price point</dt><dd>{result.points.indifference_price_point ?? '—'}</dd>
          <dt className="text-slate-500">Respondents</dt><dd>{result.n_respondents} ({result.n_out_of_order} left out: prices out of order)</dd>
        </dl>
      )}
      {result && method === 'drivers' && (
        <table className="mt-4 w-full max-w-3xl text-sm tabular-nums">
          <caption className="mb-2 text-left text-xs text-slate-500">{result.n_respondents} respondents · the attributes explain {Math.round(result.r_squared * 100)}% of {result.outcome}</caption>
          <thead className="text-left text-[11px] uppercase tracking-wider text-slate-500"><tr><th>Attribute</th><th>Correlation</th><th>Share of importance</th><th /></tr></thead>
          <tbody>{result.attributes.map((a: { variable: string; attribute: string; correlation: number; share_pct: number }) => (
            <tr key={a.variable} className="border-t border-slate-100"><td className="py-1">{a.attribute}</td><td>{a.correlation}</td>
              <td>{a.share_pct}%</td><td><Bar pct={a.share_pct} /></td></tr>))}</tbody>
        </table>
      )}
    </section>
  )
}
