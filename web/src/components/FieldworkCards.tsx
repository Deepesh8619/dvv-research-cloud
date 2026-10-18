import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { studiesApi, type DataFieldReport } from '../api/client'
import { Responses } from './FieldReportPanel'

/**
 * An uploaded export, read as the fieldwork it is.
 *
 * A data file from Decipher or Qualtrics is not a spreadsheet to profile column
 * by column: it holds every interview, how each one ended, the quota cells the
 * script counted, how long people took and who looks careless. These cards are
 * the numbers a field manager is asked for, taken from the file itself.
 */

const OUTCOMES = [
  { key: 'complete', label: 'Qualified', bar: 'bg-emerald-500', dot: 'bg-emerald-500' },
  { key: 'terminate', label: 'Screened out', bar: 'bg-rose-400', dot: 'bg-rose-400' },
  { key: 'overquota', label: 'Over quota', bar: 'bg-amber-400', dot: 'bg-amber-400' },
  { key: 'in_progress', label: 'Partial', bar: 'bg-slate-300', dot: 'bg-slate-300' },
] as const

const CHECK_LABELS: Record<string, string> = {
  speeder: 'Speeders',
  straightliner: 'Straight-lined a grid',
  duplicate: 'Duplicate of another respondent',
  gibberish: 'Gibberish open ends',
  fraud_signal: 'Copied open ends / shared connection',
}

// A quota table with this many cells is a design balancer (MaxDiff versions),
// not something anyone reads cell by cell.
const MAX_CELLS_SHOWN = 12

function minutes(seconds: number | null): string {
  if (seconds == null) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

function pct(n: number, of: number): string {
  return of > 0 ? `${((100 * n) / of).toFixed(of >= 100 ? 0 : 1).replace(/\.0$/, '')}%` : '—'
}

function fieldDates(dates: DataFieldReport['field_dates']): { value: string; hint: string } {
  if (!dates) return { value: '—', hint: 'no dates in the file' }
  const first = new Date(dates.first)
  const last = new Date(dates.last)
  const day = (d: Date) => d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
  const time = (d: Date) => d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  if (first.toDateString() === last.toDateString()) {
    return { value: day(first), hint: `${time(first)} – ${time(last)}` }
  }
  const days = Math.round((last.getTime() - first.getTime()) / 86_400_000) + 1
  return { value: `${days} days`, hint: `${day(first)} – ${day(last)}` }
}

function Card({ title, aside, children, className = '' }: {
  title: string; aside?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white p-5 shadow-panel min-w-0 ${className}`}>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {aside && <span className="text-xs text-slate-500">{aside}</span>}
      </div>
      {children}
    </section>
  )
}

function Stat({ label, value, hint, tone = 'text-slate-900', children }: {
  label: string; value: string; hint?: ReactNode; tone?: string; children?: ReactNode
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-panel min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${tone}`}>{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
      {children}
    </div>
  )
}

function Meter({ n, of, color = 'bg-indigo-500' }: { n: number; of: number; color?: string }) {
  const width = of > 0 ? Math.min(100, Math.max(n > 0 ? 2 : 0, (100 * n) / of)) : 0
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
      <div className={`h-full rounded-full ${color} motion-safe:transition-[width] motion-safe:duration-500`}
        style={{ width: `${width}%` }} />
    </div>
  )
}

function BarList({ rows, of, color, empty }: {
  rows: Array<{ key: string; label: ReactNode; n: number; sub?: string }>
  of: number; color?: string; empty: string
}) {
  if (rows.length === 0) return <p className="text-sm text-slate-500">{empty}</p>
  return (
    <ul className="space-y-2.5">
      {rows.map((r) => (
        <li key={r.key} className="min-w-0">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 break-words text-slate-800">{r.label}</span>
            <span className="shrink-0 tabular-nums text-slate-600">
              {r.n} <span className="text-slate-400">· {pct(r.n, of)}</span>
            </span>
          </div>
          {r.sub && <p className="truncate font-mono text-[11px] text-slate-400" title={r.sub}>{r.sub}</p>}
          <div className="mt-1"><Meter n={r.n} of={of} color={color} /></div>
        </li>
      ))}
    </ul>
  )
}

function Skeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-busy="true">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-24 animate-pulse rounded-lg border border-slate-200 bg-slate-50" />
      ))}
    </div>
  )
}

export default function FieldworkCards({ studyId, ready, full = false }: {
  studyId: string; ready: boolean; full?: boolean
}) {
  const [report, setReport] = useState<DataFieldReport | null>(null)
  const [state, setState] = useState<'loading' | 'none' | 'error' | 'ok'>('loading')

  useEffect(() => {
    if (!ready) {
      setState('none')
      return
    }
    let cancelled = false
    setState('loading')
    studiesApi
      .dataFieldReport(studyId)
      .then((res) => {
        if (cancelled) return
        if (res.data.available) {
          setReport(res.data)
          setState('ok')
        } else setState('none')
      })
      .catch(() => !cancelled && setState('error'))
    return () => {
      cancelled = true
    }
  }, [studyId, ready])

  if (state === 'loading') return <Skeleton />
  if (state === 'error') return <p className="text-sm text-red-600">Could not read the fieldwork from the data file.</p>
  if (state === 'none' || !report) {
    return (
      <Card title="Fieldwork">
        <p className="text-sm text-slate-500">
          No fielded data yet. Upload the export on the{' '}
          <Link className="text-indigo-600 hover:underline" to={`/projects/${studyId}/fielding`}>field monitor</Link>
          {' '}and its dispositions, quotas and quality checks appear here.
        </p>
      </Card>
    )
  }

  const d = report.dispositions
  const judged = d.complete + d.terminate
  const target = report.sample_size_target
  const loi = report.length_of_interview
  const dates = fieldDates(report.field_dates)
  const terminated = report.terminates_by_question.reduce((a, t) => a + t.n, 0)
  const devices = Object.entries(report.devices ?? {}).filter(([k]) => k !== 'unknown')
  const deviceTotal = devices.reduce((a, [, n]) => a + n, 0)
  const flagged = report.quality.n_flagged

  return (
    <div className="space-y-4" data-testid="fieldwork-cards">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Completes" value={String(d.complete)}
          hint={target ? `of ${target} needed · ${pct(d.complete, target)}` : `of ${report.starts} who started`}>
          {target ? <div className="mt-2"><Meter n={d.complete} of={target} color="bg-emerald-500" /></div> : null}
        </Stat>
        <Stat label="Incidence" value={report.incidence_pct == null ? '—' : `${report.incidence_pct}%`}
          hint={`${d.complete} of ${judged} the screener judged`} />
        <Stat label="Median interview" value={minutes(report.median_loi_sec)}
          tone={loi?.over ? 'text-amber-600' : 'text-slate-900'}
          hint={loi?.stated_minutes ? `${loi.over ? 'over' : 'within'} the ${loi.stated_minutes} min promised` : 'completes only'} />
        <Stat label="In field" value={dates.value} hint={dates.hint} />
      </div>

      <Card title="How interviews ended" aside={`${report.starts} started`}>
        <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-100" role="img"
          aria-label={OUTCOMES.map((o) => `${o.label} ${d[o.key]}`).join(', ')}>
          {OUTCOMES.map((o) => d[o.key] > 0 && (
            <div key={o.key} className={o.bar} style={{ width: `${(100 * d[o.key]) / report.starts}%` }} />
          ))}
        </div>
        <ul className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 2xl:grid-cols-4">
          {OUTCOMES.map((o) => (
            <li key={o.key} className="flex items-center gap-2 text-sm">
              <span className={`h-2 w-2 shrink-0 rounded-full ${o.dot}`} />
              <span className="text-slate-600">{o.label}</span>
              <span className="ml-auto tabular-nums font-medium text-slate-900">{d[o.key]}</span>
            </li>
          ))}
        </ul>
        {loi?.over && loi.note && <p className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">{loi.note}</p>}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Where people screened out" aside={terminated ? `${terminated} terminates` : undefined}>
          <BarList
            of={terminated}
            color="bg-rose-400"
            empty={d.terminate ? 'The file does not record which rule screened people out.' : 'Nobody screened out.'}
            rows={report.terminates_by_question.map((t) => ({ key: t.qid, label: t.qid, n: t.n, sub: t.rule }))}
          />
        </Card>

        <Card title="Soft-launch quality" aside={`${d.complete} completes checked`}>
          <p className="text-sm text-slate-700">
            <span className={`text-2xl font-semibold tabular-nums ${flagged ? 'text-amber-600' : 'text-emerald-600'}`}>{flagged}</span>
            <span className="ml-2">{flagged ? 'completes worth a look' : 'completes flagged — clean so far'}</span>
          </p>
          {report.quality.by_check.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-2">
              {report.quality.by_check.map((c) => (
                <li key={c.check} className="rounded-full bg-amber-50 px-2.5 py-1 text-xs text-amber-800 ring-1 ring-inset ring-amber-600/20">
                  {CHECK_LABELS[c.check] ?? c.check} · <span className="tabular-nums font-medium">{c.n}</span>
                </li>
              ))}
            </ul>
          )}
          {flagged > 0 && (
            <Link to={`/projects/${studyId}?tab=Cleaning`} className="mt-3 inline-block text-sm font-medium text-indigo-600 hover:underline">
              Review them in Cleaning →
            </Link>
          )}
        </Card>
      </div>

      {report.quotas.length > 0 && (
        <Card title="Quotas achieved" aside="completes counted by the script">
          <div className="grid gap-6 xl:grid-cols-2">
            {report.quotas.map((q) => {
              const total = q.cells.reduce((a, c) => a + c.n, 0)
              if (q.cells.length > MAX_CELLS_SHOWN) {
                const ns = q.cells.map((c) => c.n)
                return (
                  <div key={q.table} className="min-w-0">
                    <p className="mb-1 text-xs font-medium uppercase tracking-wider text-slate-500">{q.table}</p>
                    <p className="text-sm text-slate-600">
                      {q.cells.length} cells, {Math.min(...ns)}–{Math.max(...ns)} each — a design balancer.
                    </p>
                  </div>
                )
              }
              return (
                <div key={q.table} className="min-w-0">
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">{q.table}</p>
                  <BarList of={total} color="bg-indigo-500" empty="—"
                    rows={q.cells.map((c) => ({ key: c.cell, label: c.label, n: c.n }))} />
                </div>
              )
            })}
          </div>
          {report.quota_targets.length > 0 && (
            <div className="mt-5 border-t border-slate-100 pt-4">
              <p className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">Targets in the questionnaire</p>
              <ul className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                {report.quota_targets.map((t) => (
                  <li key={t.group + t.definition} className="flex justify-between gap-3">
                    <span className="min-w-0 break-words text-slate-700">{t.group}{t.definition ? ` · ${t.definition}` : ''}</span>
                    <span className="shrink-0 tabular-nums text-slate-500">
                      {t.target_n ?? (t.min_n != null || t.max_n != null ? `${t.min_n ?? 0}–${t.max_n ?? '∞'}` : '—')}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Partials stopped at" aside={d.in_progress ? `${d.in_progress} partials` : undefined}>
          <BarList of={d.in_progress} color="bg-slate-400"
            empty={d.in_progress ? 'Link a questionnaire to see where they stopped.' : 'Nobody abandoned mid-survey.'}
            rows={report.dropoff_by_question.map((r) => ({ key: r.qid, label: r.qid, n: r.n }))} />
        </Card>
        <Card title="Answered on">
          <BarList of={deviceTotal} color="bg-sky-500" empty="The file records no device."
            rows={devices.map(([k, n]) => ({ key: k, label: k[0].toUpperCase() + k.slice(1), n }))} />
        </Card>
      </div>

      {full && report.questions && report.questions.length > 0 && (
        <Card title="Answers, question by question" aside="completes only">
          <Responses report={report} />
        </Card>
      )}
      {!report.has_questionnaire && (
        <p className="text-xs text-slate-500">
          Add the questionnaire to see each question's answers and where partials stopped.
        </p>
      )}
    </div>
  )
}
