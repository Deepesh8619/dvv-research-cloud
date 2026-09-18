import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { PreflightResult, fieldingApi } from '../../api/client'

/** How long each check takes to "land" — long enough to read, never a wait. */
const STEP_MS = 110

const ICON: Record<string, { mark: string; ring: string }> = {
  pass: { mark: '✓', ring: 'bg-emerald-500 text-white' },
  warn: { mark: '!', ring: 'bg-amber-400 text-white' },
  fail: { mark: '✕', ring: 'bg-red-500 text-white' },
}

/**
 * The whole questionnaire checked once more, the moment before a link exists.
 *
 * Review happens question by question over days; a link pins the instrument
 * the second it is created, and from then on every problem is paid for in
 * respondents. Each check lands in turn so the list is read, not skimmed —
 * and every problem links straight to the question it is about.
 */
export default function Preflight({
  studyId,
  creating,
  onProceed,
  onCancel,
}: {
  studyId: string
  creating: boolean
  /** force = the reviewer chose to open despite failing checks. */
  onProceed: (force: boolean) => void
  onCancel: () => void
}) {
  const [result, setResult] = useState<PreflightResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [shown, setShown] = useState(0)
  const [acknowledged, setAcknowledged] = useState(false)

  useEffect(() => {
    let cancelled = false
    fieldingApi
      .preflight(studyId)
      .then((res) => !cancelled && setResult(res.data))
      .catch(() => !cancelled && setError('Could not run the pre-link check.'))
    return () => {
      cancelled = true
    }
  }, [studyId])

  // Reveal one check at a time.
  useEffect(() => {
    if (!result || shown >= result.checks.length) return
    const t = window.setTimeout(() => setShown((n) => n + 1), STEP_MS)
    return () => window.clearTimeout(t)
  }, [result, shown])

  const done = !!result && shown >= result.checks.length
  const clean = done && result.ok && result.n_warn === 0

  // Nothing to look at: go straight on.
  useEffect(() => {
    if (clean && !creating) onProceed(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clean])

  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/60 dvv-rise">
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-slate-900">Checking everything before the link opens</p>
          <p className="text-xs text-slate-500">
            The whole questionnaire, read once more as a respondent and the data file will meet it.
          </p>
        </div>
        {!done && !error && (
          <span className="h-5 w-5 animate-spin rounded-full border-2 border-slate-200 border-t-indigo-500" aria-label="Checking" />
        )}
      </div>

      {error && <p className="px-4 py-3 text-sm text-red-600">{error}</p>}

      <ul className="divide-y divide-slate-100">
        {result?.checks.slice(0, shown).map((c) => (
          <li key={c.id} className="flex gap-3 px-4 py-2.5 dvv-pop">
            <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${ICON[c.status].ring}`}>
              {ICON[c.status].mark}
            </span>
            <div className="min-w-0 flex-1">
              <p className={`text-sm ${c.status === 'pass' ? 'text-slate-600' : 'font-medium text-slate-900'}`}>{c.label}</p>
              {c.status !== 'pass' && c.detail && <p className="mt-0.5 text-xs text-slate-500">{c.detail}</p>}
              {c.qids.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {c.qids.slice(0, 12).map((qid) => (
                    <Link
                      key={qid}
                      to={`/projects/${studyId}/questionnaire?q=${encodeURIComponent(qid)}`}
                      className={`rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold ring-1 ring-inset transition-colors ${
                        c.status === 'fail'
                          ? 'bg-red-50 text-red-700 ring-red-200 hover:bg-red-100'
                          : 'bg-amber-50 text-amber-800 ring-amber-200 hover:bg-amber-100'
                      }`}
                    >
                      {qid}
                    </Link>
                  ))}
                  {c.qids.length > 12 && <span className="text-[11px] text-slate-400">+{c.qids.length - 12} more</span>}
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>

      {done && (
        <div className="border-t border-slate-200 bg-white px-4 py-3 dvv-rise">
          {clean ? (
            <p className="text-sm font-medium text-emerald-700">All {result.checks.length} checks passed — opening the link…</p>
          ) : result.ok ? (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
                I have looked at the {result.n_warn} warning{result.n_warn === 1 ? '' : 's'}
              </label>
              <div className="flex gap-2">
                <button type="button" onClick={onCancel} className="rounded-md px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800">
                  Not yet
                </button>
                <button
                  type="button"
                  disabled={!acknowledged || creating}
                  onClick={() => onProceed(false)}
                  className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white transition-all hover:bg-slate-700 active:scale-95 disabled:opacity-40"
                >
                  {creating ? 'Creating…' : 'Create link'}
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm font-medium text-red-700">
                {result.n_fail} check{result.n_fail === 1 ? '' : 's'} failed — a respondent would meet{' '}
                {result.n_fail === 1 ? 'this' : 'these'}.
              </p>
              <div className="flex gap-2">
                <Link
                  to={`/projects/${studyId}/questionnaire`}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
                >
                  Fix the questionnaire
                </Link>
                <button
                  type="button"
                  disabled={creating}
                  onClick={() => onProceed(true)}
                  title="Opens the link anyway. The decision is recorded against your name."
                  className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-40"
                >
                  Create anyway
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
