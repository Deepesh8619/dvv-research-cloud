import { useEffect, useState } from 'react'

import { FieldReport, QuestionSummary, fieldingApi } from '../api/client'

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

function pct(n: number, of: number): string {
  return of > 0 ? `${Math.round((100 * n) / of)}%` : '—'
}

// A share of the base, drawn. Width animates in so a card reads as it lands.
function Bar({ n, of }: { n: number; of: number }) {
  const width = of > 0 ? Math.max(2, (100 * n) / of) : 0
  return (
    <div className="h-2 w-full rounded-full bg-slate-100 overflow-hidden">
      <div
        className="h-full rounded-full bg-indigo-500 motion-safe:transition-[width] motion-safe:duration-500"
        style={{ width: `${n > 0 ? width : 0}%` }}
      />
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-slate-400">{label}</p>
      <p className="text-base font-semibold text-slate-800 tabular-nums">{value}</p>
    </div>
  )
}

/** One question, answered: what a researcher looks at first. */
function QuestionCard({ q }: { q: QuestionSummary }) {
  // Multi-select shares sum past 100%: each is a share of the people ASKED.
  const of = q.base
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <header className="mb-4">
        <span className="mb-1 inline-block rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-500">
          {q.qid}
        </span>
        <h4 className="text-[15px] font-medium leading-snug text-slate-900">{q.text}</h4>
        <p className="mt-1 text-sm font-medium text-indigo-600 tabular-nums">
          {q.answered} {q.answered === 1 ? 'response' : 'responses'}
          {q.type === 'rank' && <span className="text-slate-400 font-normal"> · ranked 1st</span>}
        </p>
      </header>

      {q.options && (
        <ul className="space-y-3">
          {q.options.map((o) => (
            <li key={o.code}>
              <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                <span className="text-slate-700">{o.label}</span>
                <span className="shrink-0 tabular-nums text-slate-500">
                  {o.n} · <span className="font-medium text-slate-800">{pct(o.n, of)}</span>
                </span>
              </div>
              <Bar n={o.n} of={of} />
            </li>
          ))}
        </ul>
      )}

      {q.rows && q.columns && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th />
                {q.columns.map((c) => (
                  <th key={c.code} className="px-2 pb-2 text-center text-xs font-medium text-slate-500">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {q.rows.map((r) => (
                <tr key={r.code}>
                  <td className="py-1 pr-3 text-slate-700">{r.label}</td>
                  {r.counts.map((n, i) => (
                    // A heat cell: the darker, the larger the share of the base.
                    <td key={i} className="p-0.5">
                      <div
                        className="rounded-md py-1.5 text-center tabular-nums text-slate-800"
                        style={{ background: `rgba(99,102,241,${of ? (0.08 + (0.6 * n) / of).toFixed(2) : 0.04})` }}
                        title={`${n} of ${of}`}
                      >
                        {pct(n, of)}
                      </div>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {q.numeric && (
        <div className="grid grid-cols-4 gap-2">
          <Stat label="mean" value={q.numeric.mean} />
          <Stat label="median" value={q.numeric.median} />
          <Stat label="min" value={q.numeric.min} />
          <Stat label="max" value={q.numeric.max} />
        </div>
      )}

      {q.verbatims && (
        <ul className="space-y-2">
          {q.verbatims.length === 0 && <li className="text-sm text-slate-400">No answers yet.</li>}
          {q.verbatims.map((v, i) => (
            <li key={i} className="border-l-2 border-indigo-200 pl-3 text-sm italic text-slate-600">
              “{v}”
            </li>
          ))}
        </ul>
      )}
    </article>
  )
}

/** Every answer on one table — for copying into a deck or checking a count. */
function Tabular({ questions }: { questions: QuestionSummary[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-2.5">Question</th>
            <th className="px-4 py-2.5">Answer</th>
            <th className="px-4 py-2.5 text-right">n</th>
            <th className="px-4 py-2.5 text-right">%</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {questions.flatMap((q) => {
            const lines: Array<[string, number]> =
              q.options?.map((o) => [o.label, o.n]) ??
              q.rows?.flatMap((r) =>
                r.counts.map((n, i): [string, number] => [`${r.label} — ${q.columns?.[i]?.label ?? ''}`, n]),
              ) ??
              [['answered', q.answered]]
            return lines.map(([label, n], i) => (
              <tr key={`${q.qid}-${i}`} className="hover:bg-slate-50/60">
                <td className="px-4 py-2 align-top text-slate-500">
                  {i === 0 && (
                    <>
                      <span className="font-mono text-xs">{q.qid}</span>{' '}
                      <span className="text-slate-700">{q.text}</span>
                    </>
                  )}
                </td>
                <td className="px-4 py-2 text-slate-700">{label}</td>
                <td className="px-4 py-2 text-right tabular-nums">{n}</td>
                <td className="px-4 py-2 text-right tabular-nums font-medium">{pct(n, q.base)}</td>
              </tr>
            ))
          })}
        </tbody>
      </table>
    </div>
  )
}

function Responses({ report }: { report: FieldReport }) {
  const [view, setView] = useState<'analytics' | 'tabular'>('analytics')
  const questions = report.questions ?? []
  const completes = report.dispositions.complete
  return (
    <section className="mt-6">
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <h3 className="text-xl font-semibold text-slate-900 tabular-nums">
          {completes} {completes === 1 ? 'Response' : 'Responses'}
        </h3>
        <div role="tablist" className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 text-sm">
          {(['analytics', 'tabular'] as const).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={`rounded-md px-3.5 py-1.5 capitalize transition-colors ${
                view === v ? 'bg-white font-medium text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {v}
            </button>
          ))}
        </div>
        <span className="text-xs text-slate-400">completes only</span>
      </div>
      {completes === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500">
          The first completed interview will appear here.
        </p>
      ) : view === 'analytics' ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {questions.map((q) => (
            <QuestionCard key={q.qid} q={q} />
          ))}
        </div>
      ) : (
        <Tabular questions={questions} />
      )}
    </section>
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

      {report.questions && <Responses report={report} />}
    </div>
  )
}
