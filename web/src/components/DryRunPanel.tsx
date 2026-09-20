import { useState } from 'react'

import { DryRunReport, fieldingApi } from '../api/client'

/**
 * Find out what the script does to people, before it does it to people.
 *
 * A screen-out that fires for nobody, a base that hides a question from
 * everyone, a mask that never narrows, a quota that fills at respondent 108 of
 * 500 — each is invisible in the questionnaire and obvious in one run, and each
 * costs real money once a panel is pointed at the link.
 */
const KIND_LABEL: Record<string, string> = {
  screen_out: 'Screen-out',
  base: 'Base',
  mask: 'Mask',
  quota: 'Quota',
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs uppercase tracking-wide text-slate-400">{label}</span>
      <span className={`text-xl font-semibold tabular-nums ${tone ?? 'text-slate-800'}`}>
        {value}
      </span>
    </div>
  )
}

export default function DryRunPanel({
  studyId,
  linkId,
  canRun,
}: {
  studyId: string
  linkId?: string
  canRun: boolean
}) {
  const [report, setReport] = useState<DryRunReport | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [n, setN] = useState(500)

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      const res = await fieldingApi.dryRun(studyId, n, 20260903, linkId)
      setReport(res.data)
    } catch {
      setError('The dry run could not start. The questionnaire must be approved first.')
    } finally {
      setRunning(false)
    }
  }

  const pct = (part: number) =>
    report && report.n_respondents ? Math.round((100 * part) / report.n_respondents) : 0

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-panel p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold text-slate-900">Dry run</h2>
          <p className="text-sm text-slate-600 mt-0.5 max-w-xl">
            Send synthetic respondents through the survey's own conditions and see what it
            does to them — before a vendor sends real ones. No data is saved.
          </p>
        </div>
        {canRun && (
          <div className="flex items-center gap-2 shrink-0">
            <label className="text-xs text-slate-500">
              n
              <input
                type="number"
                min={1}
                max={5000}
                value={n}
                onChange={(e) => setN(Number(e.target.value) || 1)}
                className="ml-1.5 w-20 border border-slate-300 rounded-md px-2 py-1 text-sm tabular-nums"
              />
            </label>
            <button
              onClick={run}
              disabled={running}
              className="text-sm bg-primary-500 text-white font-medium px-3.5 py-2 rounded-md hover:bg-primary-600 disabled:opacity-50"
            >
              {running ? 'Running…' : report ? 'Run again' : 'Run dry run'}
            </button>
          </div>
        )}
      </div>

      {error && <p className="text-sm text-red-600 mt-4">{error}</p>}

      {report && (
        <div className="mt-5 space-y-5">
          <div className="flex flex-wrap gap-x-10 gap-y-4">
            <Stat label="respondents" value={String(report.n_respondents)} />
            <Stat
              label="complete"
              value={`${report.dispositions.complete} · ${pct(report.dispositions.complete)}%`}
              tone="text-emerald-700"
            />
            <Stat
              label="screened out"
              value={`${report.dispositions.terminate} · ${pct(report.dispositions.terminate)}%`}
              tone="text-amber-700"
            />
            <Stat
              label="over quota"
              value={`${report.dispositions.overquota} · ${pct(report.dispositions.overquota)}%`}
              tone="text-red-600"
            />
          </div>

          {/* Arithmetic, not evidence: these cannot fire for anyone who will
              ever live, and a link cannot be opened while they stand. */}
          {report.impossible.length > 0 && (
            <div className="border border-red-200 bg-red-50/60 rounded-md px-3 py-2.5">
              <p className="text-xs font-semibold uppercase tracking-wide text-red-800">
                {report.impossible.length} condition
                {report.impossible.length === 1 ? '' : 's'} that can never be true
              </p>
              <ul className="mt-1.5 space-y-1">
                {report.impossible.map((i, idx) => (
                  <li key={`${i.qid}-${idx}`} className="text-sm text-slate-700">
                    <span className="font-mono text-xs text-slate-600">{i.qid}</span> — {i.detail}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.display_issues.length > 0 && (
            <div className="border border-amber-200 bg-amber-50/60 rounded-md px-3 py-2.5">
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-900">
                Text a respondent will not see
              </p>
              <ul className="mt-1.5 space-y-1">
                {report.display_issues.map((i, idx) => (
                  <li key={`${i.qid}-${idx}`} className="text-sm text-slate-700">
                    <span className="font-mono text-xs text-slate-600">{i.qid}</span> — {i.detail}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-400 border-b border-slate-200">
                  <th className="py-1.5 pr-3 font-medium">Condition</th>
                  <th className="py-1.5 pr-3 font-medium">Question</th>
                  <th className="py-1.5 pr-3 font-medium text-right">Fired</th>
                  <th className="py-1.5 pr-3 font-medium text-right">Of</th>
                  <th className="py-1.5 font-medium">&nbsp;</th>
                </tr>
              </thead>
              <tbody>
                {report.conditions.map((c) => (
                  <tr key={`${c.kind}-${c.qid}`} className="border-b border-slate-100">
                    <td className="py-1.5 pr-3 text-slate-500">{KIND_LABEL[c.kind] ?? c.kind}</td>
                    <td className="py-1.5 pr-3 font-mono text-xs text-slate-700">{c.qid}</td>
                    <td
                      className={`py-1.5 pr-3 text-right tabular-nums ${
                        c.fired === 0 ? 'text-red-600 font-semibold' : 'text-slate-800'
                      }`}
                    >
                      {c.fired}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-slate-500">
                      {c.eligible}
                    </td>
                    <td className="py-1.5 text-xs text-slate-500">
                      {c.fired === 0
                        ? 'nothing exercised it'
                        : c.filled_at
                          ? `filled at respondent #${c.filled_at}`
                          : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {report.conditions.length === 0 && (
            <p className="text-sm text-slate-500">
              This questionnaire states no screen-outs, bases, masks or quotas — so there is
              nothing for a dry run to exercise.
            </p>
          )}

          <p className="text-xs text-slate-400">
            {report.qualify_rate !== undefined && (
              <>
                {Math.round(report.qualify_rate * 100)}% of synthetic respondents are on-target
                and pass every screener; each of the rest fails one screen-out on purpose, so
                every rule is tested. These are not an incidence forecast.{' '}
              </>
            )}
            Seed {report.seed} — the same survey and seed always give the same run.
            {report.never_fired.length > 0 && (
              <>
                {' '}
                Nothing exercised {report.never_fired.join(', ')}; with {report.n_respondents}{' '}
                synthetic respondents that is evidence, not proof — a narrow path may simply
                have been missed.
              </>
            )}
          </p>
        </div>
      )}
    </div>
  )
}
