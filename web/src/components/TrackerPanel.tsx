import { useEffect, useState } from 'react'

import { downloadFile, studiesApi, type StackCheck, type Study, type WaveTrend } from '../api/client'

/**
 * Other waves or countries of this tracker: what drifted between the files
 * (a dropped question, an added code, a code that now means something else),
 * what moved significantly against one earlier wave, and one stacked SPSS file
 * with a SOURCE variable — refused while any question's codes disagree.
 */
export default function TrackerPanel({ study }: { study: Study }) {
  const [others, setOthers] = useState<Study[]>([])
  const [picked, setPicked] = useState<string[]>([])
  const [check, setCheck] = useState<StackCheck | null>(null)
  const [moves, setMoves] = useState<WaveTrend[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    studiesApi.list(0, 100)
      .then((r) => setOthers(r.data.studies.filter((s) => s.id !== study.id && s.excel_path)))
      .catch(() => setOthers([]))
  }, [study.id])

  if (others.length === 0) return null
  const toggle = (id: string) => {
    setCheck(null)
    setMoves(null)
    setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id])
  }
  const compare = () => {
    setError(null)
    studiesApi.stackCheck(study.id, picked).then((r) => setCheck(r.data))
      .catch((e) => setError(e?.response?.data?.detail ?? 'Could not compare these studies.'))
    if (picked.length === 1) {
      studiesApi.tracking(study.id, picked[0]).then((r) => setMoves(r.data.variables)).catch(() => setMoves(null))
    }
  }
  const questions = check?.changed.filter((c) => !c.script) ?? []
  const moved = (moves ?? []).filter((v) => v.n_significant > 0).slice(0, 8)

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-panel" data-testid="tracker">
      <h2 className="font-semibold text-slate-900">Waves and countries</h2>
      <p className="mt-1 max-w-2xl text-sm text-slate-500">
        Pick the other files of this tracker. Their codes are checked against this one before anything is trended or
        pooled.
      </p>
      <div className="mt-3 flex max-h-40 flex-wrap gap-2 overflow-y-auto">
        {others.map((s) => (
          <label key={s.id} className="flex items-center gap-1.5 rounded border border-slate-200 px-2 py-1 text-sm">
            <input type="checkbox" checked={picked.includes(s.id)} onChange={() => toggle(s.id)} />
            {s.client_name}
          </label>
        ))}
      </div>
      <button type="button" disabled={picked.length === 0} onClick={compare}
        className="mt-3 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
        Compare
      </button>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      {check && (
        <div className="mt-4 space-y-3 text-sm">
          <p className="text-slate-700">
            {check.n_in_every_source} of {check.n_variables} variables are in every file.{' '}
            {check.n_breaking > 0
              ? <span className="text-red-700">
                  {check.n_breaking} {check.n_breaking === 1 ? 'question means' : 'questions mean'} different things in
                  different files.
                </span>
              : <span className="text-emerald-700">No question changed meaning.</span>}
          </p>
          {questions.length > 0 && (
            <ul className="max-h-60 space-y-1 overflow-y-auto">
              {questions.map((c) => (
                <li key={c.variable + c.source} className={c.breaking ? 'text-red-800' : 'text-slate-600'}>
                  <span className="font-mono">{c.variable}</span> ({c.source}): {c.problems.join('; ')}
                </li>
              ))}
            </ul>
          )}
          {moved.length > 0 && (
            <div>
              <p className="font-medium text-slate-900">Significant moves since {others.find((s) => s.id === picked[0])?.client_name}</p>
              <ul className="mt-1 space-y-1 text-slate-700">
                {moved.map((v) => v.deltas.filter((d) => d.significant).slice(0, 1).map((d) => (
                  <li key={v.name}>
                    {v.label}: {d.label} {d.pct_baseline}% → {d.pct_current}% ({d.delta > 0 ? '+' : ''}{d.delta} points)
                  </li>
                )))}
              </ul>
            </div>
          )}
          <button type="button" disabled={check.n_breaking > 0}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
            onClick={() => downloadFile(
              `/studies/${study.id}/stack.sav?${picked.map((p) => `with=${encodeURIComponent(p)}`).join('&')}`,
              `${study.id}_stacked.sav`)}>
            Download stacked SPSS file
          </button>
        </div>
      )}
    </section>
  )
}
