import { useEffect, useState } from 'react'
import { errorMessage, studiesApi, type WeightingReport, type WeightingStatus, type WeightTargets } from '../api/client'

/**
 * Rim weighting: pick the variables, type the population shares, preview what it
 * does to the sample (efficiency, extreme weights, achieved shares), then apply.
 * Tables use the weight (dvv_weight) when asked for weighted figures.
 */
export default function WeightingPanel({ studyId }: { studyId: string }) {
  const [status, setStatus] = useState<WeightingStatus | null>(null)
  const [targets, setTargets] = useState<WeightTargets>({})
  const [preview, setPreview] = useState<WeightingReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () =>
    studiesApi.weighting(studyId).then((r) => {
      setStatus(r.data)
      if (r.data.targets) setTargets(r.data.targets)
    }).catch((e) => setError(errorMessage(e) || 'Could not load the weighting.'))
  useEffect(() => { void load() }, [studyId])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!status) return null
  if (status.candidates.length === 0) return null  // no data processed yet

  const chosen = Object.keys(targets)
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(errorMessage(e) || 'That did not work.')
    } finally {
      setBusy(false)
    }
  }
  const total = (v: string) => Object.values(targets[v] ?? {}).reduce((a, b) => a + (Number(b) || 0), 0)
  const report = preview ?? status.report ?? null

  return (
    <section className="bg-white rounded-lg border border-slate-200 shadow-panel p-6" data-testid="weighting-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold text-slate-900">Weighting</h2>
        <p className="text-xs text-slate-500">
          {status.applied
            ? `Applied as dvv_weight${status.applied_by ? ` by ${status.applied_by}` : ''}`
            : 'Unweighted: every respondent counts once'}
        </p>
      </div>
      {status.stale && <p className="mt-2 rounded bg-amber-50 px-3 py-2 text-xs text-amber-800">{status.stale}</p>}

      <div className="mt-4 space-y-3">
        {chosen.map((v) => {
          const c = status.candidates.find((x) => x.name === v)
          return (
            <fieldset key={v} className="rounded-md border border-slate-200 p-3">
              <legend className="px-1 text-xs font-medium text-slate-700">{v} · {c?.label}</legend>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {(c?.codes ?? []).map((code) => (
                  <label key={code.code} className="text-xs text-slate-600">
                    {code.label}
                    <input type="number" min={0} step="0.1" inputMode="decimal"
                      value={targets[v]?.[String(code.code)] ?? ''}
                      onChange={(e) => {
                        setPreview(null)
                        setTargets((t) => ({ ...t, [v]: { ...t[v], [String(code.code)]: Number(e.target.value) } }))
                      }}
                      className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-sm tabular-nums" />
                  </label>
                ))}
              </div>
              <div className="mt-2 flex justify-between text-[11px] text-slate-500">
                <span className={Math.abs(total(v) - 100) > 0.5 ? 'text-amber-700' : ''}>
                  Adds up to {total(v).toFixed(1)}%{Math.abs(total(v) - 100) > 0.5 ? ' (scaled to 100%)' : ''}
                </span>
                <button type="button" className="hover:underline" onClick={() => {
                  setPreview(null)
                  setTargets(({ [v]: _drop, ...rest }) => rest)
                }}>Remove</button>
              </div>
            </fieldset>
          )
        })}
        <select aria-label="Add a weighting variable" value=""
          onChange={(e) => {
            const v = e.target.value
            if (!v) return
            setPreview(null)
            setTargets((t) => ({ ...t, [v]: {} }))
          }}
          className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-700">
          <option value="">Add a variable to weight on…</option>
          {status.candidates.filter((c) => !chosen.includes(c.name)).map((c) => (
            <option key={c.name} value={c.name}>{c.name} · {c.label.slice(0, 50)}</option>
          ))}
        </select>
      </div>

      {report && (
        <div className="mt-4 rounded-md bg-slate-50 p-3 text-xs text-slate-700" data-testid="weighting-report">
          <p>
            <span className="font-medium">{preview ? 'Preview' : 'Applied'}:</span> efficiency{' '}
            <span className={report.efficiency_pct < 70 ? 'font-semibold text-amber-700' : 'font-semibold'}>
              {report.efficiency_pct}%
            </span>{' '}
            (effective sample {report.effective_n} of {report.n}) · weights {report.min_weight}–{report.max_weight}
            {report.converged ? '' : ' · did not fully converge'}
          </p>
          {report.efficiency_pct < 70 && (
            <p className="mt-1 text-amber-700">Low efficiency: the weights are working hard; results will be noisier.</p>
          )}
          <table className="mt-2 w-full">
            <thead className="text-left text-slate-500">
              <tr><th className="font-normal">Category</th><th className="font-normal">Sample</th><th className="font-normal">Weighted</th><th className="font-normal">Target</th></tr>
            </thead>
            <tbody>
              {Object.entries(report.targets).flatMap(([v, cells]) =>
                Object.entries(cells).map(([code, c]) => (
                  <tr key={`${v}-${code}`}>
                    <td>{v} · {c.label}</td>
                    <td className="tabular-nums">{c.unweighted_pct}%</td>
                    <td className="tabular-nums">{c.weighted_pct}%</td>
                    <td className="tabular-nums">{c.target_pct}%</td>
                  </tr>
                )))}
            </tbody>
          </table>
        </div>
      )}

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" disabled={busy || chosen.length === 0}
          onClick={() => run(async () => setPreview((await studiesApi.previewWeighting(studyId, targets)).data))}
          className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 disabled:opacity-40">
          Preview
        </button>
        <button type="button" disabled={busy || !preview}
          onClick={() => run(async () => {
            await studiesApi.applyWeighting(studyId, targets)
            setPreview(null)
            await load()
          })}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
          Apply weighting
        </button>
        {status.applied && (
          <button type="button" disabled={busy}
            onClick={() => run(async () => {
              await studiesApi.removeWeighting(studyId)
              setTargets({})
              await load()
            })}
            className="rounded px-3 py-1.5 text-sm text-slate-500 hover:text-red-600">
            Remove weighting
          </button>
        )}
      </div>
    </section>
  )
}
