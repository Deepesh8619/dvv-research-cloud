import { useCallback, useEffect, useState } from 'react'

import { SoftLaunchCheck, fieldingApi } from '../api/client'

/**
 * The decision point of a soft launch.
 *
 * A hold is only worth taking if something is inspected during it, and the
 * inspection is only worth trusting if it says what it could NOT look at. So
 * this panel always shows three things together: what was checked, what was
 * found, and what was skipped — then offers Release, which is the moment a
 * person takes responsibility for spending the rest of the sample.
 */
const SEVERITY_STYLES: Record<string, string> = {
  error: 'border-red-200 bg-red-50 text-red-800',
  warning: 'border-amber-200 bg-amber-50 text-amber-800',
  info: 'border-slate-200 bg-slate-50 text-slate-600',
}

export default function SoftLaunchPanel({
  studyId,
  linkId,
  canWrite,
  onChanged,
}: {
  studyId: string
  linkId: string
  canWrite: boolean
  onChanged: () => void
}) {
  const [check, setCheck] = useState<SoftLaunchCheck | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setError(null)
    fieldingApi
      .softLaunchCheck(studyId, linkId)
      .then((res) => setCheck(res.data))
      .catch(() => setError('Could not run the soft-launch check.'))
  }, [studyId, linkId])

  useEffect(load, [load])

  const release = async () => {
    if (
      !window.confirm(
        'Release the hold? The link starts admitting respondents again and the rest of the sample gets fielded.',
      )
    )
      return
    setBusy(true)
    try {
      await fieldingApi.setSoftLaunch(studyId, linkId, null)
      onChanged()
      load()
    } catch {
      setError('Could not release the hold.')
    } finally {
      setBusy(false)
    }
  }

  if (error) return <p className="text-sm text-red-600 mt-3">{error}</p>
  if (!check) return <p className="text-sm text-slate-400 mt-3">Checking the launch batch…</p>

  const { crosscheck: cc } = check
  const errors = cc.findings.filter((f) => f.severity === 'error')
  const warnings = cc.findings.filter((f) => f.severity === 'warning')
  const ranNothing = Object.keys(cc.checked).length === 0

  return (
    <div className="mt-3 border-t border-slate-100 pt-4 space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <span className="text-sm text-slate-700">
          <span className="font-semibold tabular-nums">{check.n_completes}</span> complete
          {check.soft_launch_limit !== null && (
            <span className="text-slate-500"> of {check.soft_launch_limit} in the launch batch</span>
          )}
        </span>
        <span className="text-xs text-slate-500">
          {check.n_checked_rows} row{check.n_checked_rows === 1 ? '' : 's'} checked (terminates
          included — they are the evidence the screen-outs fired)
        </span>
      </div>

      {/* "Nothing found" and "could not look" must never both render as silence. */}
      {ranNothing ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          No check could run yet{cc.skipped.length > 0 && `: ${cc.skipped.join('; ')}`}. This is not
          a pass — nothing has been examined.
        </div>
      ) : errors.length === 0 ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          The launch batch does what the questionnaire says.{' '}
          {Object.entries(cc.checked)
            .map(([k, n]) => `${n} ${k}`)
            .join(', ')}{' '}
          checked{warnings.length > 0 && `, ${warnings.length} thing(s) worth a look below`}.
        </div>
      ) : (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {errors.length} thing{errors.length === 1 ? '' : 's'} the data and the questionnaire
          disagree on. Fix the script before releasing — after release the sample is spent.
        </div>
      )}

      {cc.findings.length > 0 && (
        <ul className="space-y-2">
          {cc.findings.map((f, i) => (
            <li
              key={`${f.qid}-${f.kind}-${i}`}
              className={`rounded-md border p-2.5 text-sm ${SEVERITY_STYLES[f.severity] ?? SEVERITY_STYLES.info}`}
            >
              <span className="font-mono text-xs mr-2">{f.qid}</span>
              {f.detail}
              {f.n_rows > 0 && (
                <span className="text-xs opacity-70"> ({f.n_rows} respondent(s))</span>
              )}
            </li>
          ))}
        </ul>
      )}

      {cc.skipped.length > 0 && !ranNothing && (
        <p className="text-xs text-slate-500">Could not check: {cc.skipped.join('; ')}</p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={load}
          className="text-xs border border-slate-300 text-slate-700 font-medium px-3 py-1.5 rounded-md hover:bg-slate-50"
        >
          Re-run check
        </button>
        {canWrite && check.soft_launch_limit !== null && (
          <button
            type="button"
            onClick={release}
            disabled={busy}
            className="text-xs bg-primary-500 text-white font-medium px-3 py-1.5 rounded-md hover:bg-primary-600 disabled:opacity-50"
          >
            {busy ? 'Releasing…' : 'Release to full sample'}
          </button>
        )}
        {errors.length > 0 && canWrite && check.soft_launch_limit !== null && (
          <span className="text-xs text-red-600">Releasing now fields a script with known errors.</span>
        )}
      </div>
    </div>
  )
}
