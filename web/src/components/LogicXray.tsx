import { useState } from 'react'

import { DryRunReport } from '../api/client'
import InfoTip from './ui/InfoTip'

/**
 * The questionnaire as a line of stations with the simulated paths drawn
 * between them. Plain SVG; line thickness is the share of respondents.
 * Station order comes from the report (first appearance in the paths, then the
 * questions nobody reaches) because the panel has no question list.
 */
const GAP = 84
const X0 = 50
// Arcs grow with the number of stations they skip, capped so a jump from the
// first question to the last stays inside the drawing instead of off the top.
const arcHeight = (span: number) => Math.min(96, 18 + 12 * span)

const ENDS = new Set(['END', 'TERMINATE', 'OVERQUOTA'])

/**
 * Questionnaire order, recovered from the paths: every transition points
 * forward, so a topological sort of them puts each question after everything
 * that can precede it. Ordering by first appearance in the commonest path put
 * a question most people skip after the questions that follow it.
 */
export function stationOrder(report: DryRunReport): string[] {
  const seen: string[] = []
  const add = (q: string) => { if (!ENDS.has(q) && !seen.includes(q)) seen.push(q) }
  for (const p of report.paths ?? []) p.qids.forEach(add)
  for (const t of report.transitions ?? []) { add(t.from); add(t.to) }
  const after = new Map(seen.map((q) => [q, new Set<string>()]))
  const indegree = new Map(seen.map((q) => [q, 0]))
  for (const t of report.transitions ?? []) {
    if (ENDS.has(t.to) || t.from === t.to || after.get(t.from)!.has(t.to)) continue
    after.get(t.from)!.add(t.to)
    indegree.set(t.to, indegree.get(t.to)! + 1)
  }
  const order: string[] = []
  const ready = seen.filter((q) => indegree.get(q) === 0)
  while (ready.length) {
    const q = ready.shift()!
    order.push(q)
    for (const n of after.get(q)!) {
      indegree.set(n, indegree.get(n)! - 1)
      if (indegree.get(n) === 0) ready.push(n)
    }
    ready.sort((a, b) => seen.indexOf(a) - seen.indexOf(b))
  }
  for (const q of seen) if (!order.includes(q)) order.push(q) // a loop: keep first-seen order
  for (const q of report.questions_never_shown) if (!order.includes(q)) order.push(q)
  return [...order, 'END']
}

export default function LogicXray({ report }: { report: DryRunReport }) {
  const transitions = report.transitions ?? []
  const exits = report.exits ?? []
  const unreachable = report.questions_never_shown
  const total = report.n_respondents || 1

  const stations = stationOrder(report)

  const [selected, setSelected] = useState<string | null>(null)
  const x = (q: string) => X0 + stations.indexOf(q) * GAP
  const width = X0 * 2 + (stations.length - 1) * GAP
  const sw = (n: number) => Math.max(1.5, (8 * n) / total)
  const reach = (q: string) =>
    transitions.filter((t) => t.from === q).reduce((a, t) => a + t.n, 0)
  const next = selected ? transitions.filter((t) => t.from === selected) : []
  const exitX = (q: string) => x(q)
  const span = (t: { from: string; to: string }) =>
    Math.abs(stations.indexOf(t.to) - stations.indexOf(t.from))
  const Y = 16 + Math.max(0, ...transitions.map((t) => (span(t) > 1 ? arcHeight(span(t)) : 0)))
  const EXIT_Y = Y + 70

  return (
    <div className="border border-slate-200 rounded-md p-3">
      <h3 className="text-sm font-semibold text-slate-900">
        Logic X-ray<InfoTip id="logic_xray" />
      </h3>
      <p className="text-xs text-slate-500 mt-0.5">
        Line thickness = share of simulated respondents. Dashed = nobody reaches it.
      </p>
      <div className="mt-2 flex flex-col gap-3">
        <div className="overflow-x-auto">
          <svg
            width={width}
            height={EXIT_Y + 40}
            role="group"
            aria-label="Logic X-ray: paths simulated respondents took between questions"
            className="text-slate-500"
          >
            {transitions.map((t, i) => {
              if (!stations.includes(t.from) || !stations.includes(t.to)) return null
              const a = x(t.from)
              const b = x(t.to)
              const skip = span(t) > 1
              const common = {
                key: i,
                fill: 'none',
                stroke: 'currentColor',
                strokeWidth: sw(t.n),
                strokeOpacity: 0.6,
              }
              return skip ? (
                <path
                  {...common}
                  data-testid="arc"
                  d={`M${a},${Y} C${a},${Y - arcHeight(span(t))} ${b},${Y - arcHeight(span(t))} ${b},${Y}`}
                />
              ) : (
                <line {...common} data-testid="segment" x1={a} y1={Y} x2={b} y2={Y} />
              )
            })}
            {exits.map((e) => (
              <g key={`${e.qid}-${e.disposition}`} data-testid="exit">
                <line
                  x1={exitX(e.qid)}
                  y1={Y}
                  x2={exitX(e.qid)}
                  y2={EXIT_Y}
                  stroke="currentColor"
                  strokeWidth={sw(e.n)}
                  strokeOpacity={0.6}
                />
                <rect x={exitX(e.qid) - 6} y={EXIT_Y} width={12} height={12} className="fill-amber-500" />
                <text x={exitX(e.qid)} y={EXIT_Y + 26} textAnchor="middle" fontSize={10} className="fill-slate-600">
                  {e.disposition === 'terminate'
                    ? 'TERMINATE'
                    : e.disposition === 'overquota'
                      ? 'OVERQUOTA'
                      : e.disposition}{' '}
                  {e.n}
                </text>
              </g>
            ))}
            {stations.map((q) => {
              const dead = unreachable.includes(q)
              const on = selected === q
              return (
                <g
                  key={q}
                  data-testid="station"
                  data-unreachable={dead || undefined}
                  role="button"
                  tabIndex={0}
                  aria-pressed={on}
                  aria-label={dead ? `${q}, nobody reaches it` : q}
                  onClick={() => setSelected(q)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setSelected(q)
                    }
                  }}
                  className="cursor-pointer outline-none focus-visible:[&>circle]:stroke-primary-500 focus-visible:[&>circle]:stroke-[3]"
                >
                  <title>{q}</title>
                  <circle
                    cx={x(q)}
                    cy={Y}
                    r={on ? 11 : 9}
                    strokeWidth={2}
                    strokeDasharray={dead ? '3 2' : undefined}
                    className={`${on ? 'fill-primary-500' : 'fill-white'} ${
                      dead ? 'stroke-amber-600' : 'stroke-slate-500'
                    }`}
                  />
                  <text x={x(q)} y={Y + 28} textAnchor="middle" fontSize={10} className="fill-slate-700 font-mono">
                    {q}
                  </text>
                  {dead && (
                    <text x={x(q)} y={Y + 41} textAnchor="middle" fontSize={9} className="fill-amber-700">
                      nobody reaches it
                    </text>
                  )}
                </g>
              )
            })}
          </svg>
        </div>
        <aside className="text-sm text-slate-700 border-t border-slate-100 pt-3" aria-live="polite" data-testid="xray-panel">
          {selected ? (
            <>
              <p className="font-semibold font-mono text-xs">{selected}</p>
              {unreachable.includes(selected) ? (
                <p className="mt-1">Nobody reaches this question.</p>
              ) : selected === 'END' ? (
                <p className="mt-1">The end of the survey.</p>
              ) : (
                <>
                  <p className="mt-1">
                    {Math.round((100 * reach(selected)) / total)}% of respondents reach it ({reach(selected)}).
                  </p>
                  <ul className="mt-1 space-y-0.5">
                    {next.map((t) => (
                      <li key={t.to}>
                        then {t.to}: {t.n}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          ) : (
            <p className="text-slate-500">Select a question to see where respondents go next.</p>
          )}
          {unreachable.length > 0 && (
            <p className="mt-3">
              <span className="font-semibold">Nobody reaches:</span> {unreachable.join(', ')}
            </p>
          )}
          {exits.length > 0 && (
            <ul className="mt-3 space-y-0.5">
              {exits.map((e) => (
                <li key={`${e.qid}-${e.disposition}`}>
                  {e.n} {e.disposition} at {e.qid}
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  )
}
