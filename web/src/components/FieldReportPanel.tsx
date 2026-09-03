import { useEffect, useState } from 'react'

import { FieldReport, fieldingApi } from '../api/client'

/**
 * The numbers a researcher is asked for the morning after a launch.
 *
 * All of it already lived in the sessions and had simply never been counted.
 * Incidence is the one that costs money: a study bid at 40% that fields at 12%
 * is a budget problem the day it starts.
 */
function minutes(seconds: number | null): string {
  if (seconds === null) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function Row({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs uppercase tracking-wide text-slate-400">{label}</span>
      <span className="text-lg font-semibold text-slate-800 tabular-nums">{value}</span>
      {hint && <span className="text-xs text-slate-500">{hint}</span>}
    </div>
  )
}

function Breakdown({
  title,
  rows,
  empty,
}: {
  title: string
  rows: Array<{ key: string; n: number }>
  empty: string
}) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-slate-400 mb-1">{title}</p>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">{empty}</p>
      ) : (
        <ul className="space-y-0.5">
          {rows.map((r) => (
            <li key={r.key} className="text-sm text-slate-700 flex gap-2">
              <span className="tabular-nums w-10 text-right text-slate-800">{r.n}</span>
              <span className="font-mono text-xs pt-0.5 text-slate-600">{r.key}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function FieldReportPanel({
  studyId,
  linkId,
}: {
  studyId: string
  linkId: string
}) {
  const [report, setReport] = useState<FieldReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fieldingApi
      .fieldReport(studyId, linkId)
      .then((res) => {
        if (!cancelled) setReport(res.data)
      })
      .catch(() => {
        if (!cancelled) setError('Could not load the field report.')
      })
    return () => {
      cancelled = true
    }
  }, [studyId, linkId])

  if (error) return <p className="text-sm text-red-600 mt-3">{error}</p>
  if (!report) return <p className="text-sm text-slate-400 mt-3">Loading the field report…</p>

  const d = report.dispositions
  return (
    <div className="mt-3 border-t border-slate-100 pt-4 space-y-4">
      <div className="flex flex-wrap gap-x-10 gap-y-4">
        <Row label="starts" value={String(report.starts)} />
        <Row
          label="incidence"
          // null and 0% are different answers: "not known yet" vs "nobody qualifies".
          value={report.incidence_pct === null ? 'not yet' : `${report.incidence_pct}%`}
          hint={
            report.incidence_pct === null
              ? 'nobody judged yet'
              : `${d.complete} of ${d.complete + d.terminate} judged`
          }
        />
        <Row
          label="median interview"
          value={minutes(report.median_loi_sec)}
          hint="completes only"
        />
        <Row label="in progress" value={String(d.in_progress)} />
      </div>

      <div className="grid gap-5 sm:grid-cols-3">
        <Breakdown
          title="Screened out at"
          rows={report.terminates_by_question.map((t) => ({ key: t.qid, n: t.n }))}
          empty="Nobody screened out."
        />
        <Breakdown
          title="Over quota on"
          rows={report.overquota_by_cell.map((o) => ({ key: o.cell, n: o.n }))}
          empty="No cell has turned anyone away."
        />
        <Breakdown
          title="Last answered before dropping"
          rows={report.dropoff_by_question.map((o) => ({ key: o.qid, n: o.n }))}
          empty="Nobody abandoned mid-survey."
        />
      </div>
    </div>
  )
}
