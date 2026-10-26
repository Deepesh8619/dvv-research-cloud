import { useCallback, useEffect, useMemo, useState } from 'react'

import { questionnaireApi, type Expression, type Question } from '../api/client'
import { describeCondition } from './RoutingEditor'
import InfoTip from './ui/InfoTip'

/**
 * The questionnaire's logic as a map, straight after upload: every question in
 * order, every dependency drawn as a line from the question it depends on, and
 * what the solver proved about who can reach each one. Connecting two questions
 * here writes the same routing the question editor writes, through the same
 * update route, so a drawn condition is validated exactly like a typed one.
 */

type Kind = 'shows' | 'screens' | 'masks' | 'pipes'
interface Edge { from: string; to: string; kind: Kind }
interface Proof {
  unreachable: Array<{ qid: string; why: string }>
  not_provable: Array<{ qid: string; reason: string }>
  always_screened_out?: string[]
}

const ROW = 46
const TOP = 16
const LANE = 11
const MAX_LANES = 14
const STYLE: Record<Kind, { stroke: string; dash?: string; label: string }> = {
  shows: { stroke: 'stroke-primary-500', label: 'Shown only if' },
  screens: { stroke: 'stroke-amber-600', label: 'Screens out on' },
  masks: { stroke: 'stroke-sky-500', dash: '5 3', label: 'Options come from' },
  pipes: { stroke: 'stroke-slate-400', dash: '2 3', label: 'Text pipes in' },
}

function refs(e: Expression | null | undefined): string[] {
  if (!e) return []
  return [...(e.qid ? [e.qid] : []), ...(e.children ?? []).flatMap(refs)]
}

function techniques(q: Question): { masks: string[]; pipes: string[] } {
  const t = ((q.validation ?? {}) as { techniques?: { masks?: Array<Record<string, string>>; pipes?: Array<Record<string, string>> } }).techniques ?? {}
  return {
    masks: (t.masks ?? []).flatMap((m) => [m.source_qid, m.exclude_qid]).filter(Boolean) as string[],
    pipes: (t.pipes ?? []).map((p) => p.source_qid).filter(Boolean) as string[],
  }
}

/** Every dependency, pointing from the question depended on to the one that depends. */
export function edgesOf(questions: Question[]): Edge[] {
  const at = new Map(questions.map((q) => [q.qid.toUpperCase(), q.qid]))
  const out: Edge[] = []
  const seen = new Set<string>()
  const add = (from: string, to: string, kind: Kind) => {
    const src = at.get(from.toUpperCase())
    const key = `${src}>${to}>${kind}`
    if (!src || src === to || seen.has(key)) return
    seen.add(key)
    out.push({ from: src, to, kind })
  }
  for (const q of questions) {
    refs(q.show_if).forEach((r) => add(r, q.qid, 'shows'))
    refs(q.terminate_if).forEach((r) => add(r, q.qid, 'screens'))
    const t = techniques(q)
    t.masks.forEach((r) => add(r, q.qid, 'masks'))
    t.pipes.forEach((r) => add(r, q.qid, 'pipes'))
  }
  return out
}

/** Lanes so lines that overlap in height never share one (greedy, shortest first). */
function lanes(edges: Edge[], pos: Map<string, number>): Map<Edge, number> {
  const used: Array<Array<[number, number]>> = []
  const out = new Map<Edge, number>()
  const span = (e: Edge) => [Math.min(pos.get(e.from)!, pos.get(e.to)!), Math.max(pos.get(e.from)!, pos.get(e.to)!)] as [number, number]
  for (const e of [...edges].sort((a, b) => (span(a)[1] - span(a)[0]) - (span(b)[1] - span(b)[0]))) {
    const [lo, hi] = span(e)
    let lane = used.findIndex((iv) => iv.every(([a, b]) => hi < a || lo > b))
    if (lane === -1) lane = used.length < MAX_LANES ? used.push([]) - 1 : MAX_LANES - 1
    used[lane].push([lo, hi])
    out.set(e, lane)
  }
  return out
}

function sentence(e: Expression | null, questions: Question[]): string {
  if (!e) return ''
  if (e.op === 'and' || e.op === 'or') {
    return (e.children ?? []).map((c) => sentence(c, questions)).filter(Boolean).join(e.op === 'and' ? ', and ' : ', or ')
  }
  if (e.op === 'not') return `not (${(e.children ?? []).map((c) => sentence(c, questions)).join(', ')})`
  return describeCondition(e, questions) || (e.verbatim_text ?? '')
}

export function conditionFor(source: Question, codes: number[], numOp: 'gt' | 'lt' | 'eq', num: string): Expression | null {
  if (source.type === 'numeric' && !source.options.length) {
    const value = Number(num)
    return num.trim() === '' || Number.isNaN(value) ? null : { op: numOp, qid: source.qid, value }
  }
  if (!codes.length) return null
  if (source.type === 'multi') {
    return codes.length === 1
      ? { op: 'selected', qid: source.qid, value: codes[0] }
      : { op: 'or', children: codes.map((c) => ({ op: 'selected', qid: source.qid, value: c })) }
  }
  return codes.length === 1 ? { op: 'eq', qid: source.qid, value: codes[0] } : { op: 'in', qid: source.qid, values: codes }
}

export function combine(existing: Expression | null, added: Expression, mode: 'add' | 'replace'): Expression {
  if (!existing || mode === 'replace') return added
  return existing.op === 'and' ? { op: 'and', children: [...(existing.children ?? []), added] } : { op: 'and', children: [existing, added] }
}

const connectable = (q: Question) => q.options.length > 0 || q.type === 'numeric'

export default function LogicMap({ studyId, canWrite }: { studyId: string; canWrite: boolean }) {
  const [questions, setQuestions] = useState<Question[] | null>(null)
  const [proof, setProof] = useState<Proof | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [from, setFrom] = useState<string | null>(null) // connect mode: the source
  const [to, setTo] = useState<string | null>(null)
  const [codes, setCodes] = useState<number[]>([])
  const [numOp, setNumOp] = useState<'gt' | 'lt' | 'eq'>('gt')
  const [num, setNum] = useState('')
  const [mode, setMode] = useState<'add' | 'replace'>('add')
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmRemove, setConfirmRemove] = useState(false)

  const load = useCallback(() => {
    questionnaireApi.questions(studyId)
      .then((r) => { setQuestions(r.data.questions); setError(null) })
      .catch((e) => setError(e?.response?.status === 404 ? 'Upload or build a questionnaire to see its logic.' : 'Could not load the questionnaire.'))
    questionnaireApi.logic(studyId).then((r) => setProof(r.data.proof)).catch(() => setProof(null))
  }, [studyId])
  useEffect(load, [load])

  const pos = useMemo(() => new Map((questions ?? []).map((q, i) => [q.qid, i])), [questions])
  const edges = useMemo(() => edgesOf(questions ?? []), [questions])
  const laneOf = useMemo(() => lanes(edges, pos), [edges, pos])

  if (error) return <section className="rounded-lg border border-slate-200 bg-white p-5 text-sm text-slate-600 shadow-panel">{error}</section>
  if (!questions) return <section className="rounded-lg border border-slate-200 bg-white p-5 text-sm text-slate-400 shadow-panel">Loading the questionnaire…</section>

  const byQid = new Map(questions.map((q) => [q.qid, q]))
  const unreachable = new Map((proof?.unreachable ?? []).map((u) => [u.qid, u.why]))
  const unproved = new Map((proof?.not_provable ?? []).map((u) => [u.qid, u.reason]))
  const nLanes = Math.min(MAX_LANES, Math.max(0, ...[...laneOf.values()].map((l) => l + 1)))
  const nodeX = 20 + nLanes * LANE
  const height = TOP * 2 + questions.length * ROW
  const y = (qid: string) => TOP + pos.get(qid)! * ROW + ROW / 2
  const sel = selected ? byQid.get(selected) : undefined
  const source = from ? byQid.get(from) : undefined
  const target = to ? byQid.get(to) : undefined
  const pending = source && target ? conditionFor(source, codes, numOp, num) : null

  const resetConnect = () => { setFrom(null); setTo(null); setCodes([]); setNum(''); setMode('add') }
  const pick = (qid: string) => {
    setNotice(null)
    setConfirmRemove(false)
    if (from && qid !== from && pos.get(qid)! > pos.get(from)!) { setTo(qid); setSelected(qid); return }
    if (from) return
    setSelected(qid)
  }
  const save = async (qid: string, showIf: Expression | null, done: string) => {
    setSaving(true)
    try {
      await questionnaireApi.update(studyId, qid, { show_if: showIf } as never)
      setNotice(`${done} The questionnaire is back in review; approve it again before fielding.`)
      resetConnect()
      setConfirmRemove(false)
      load()
    } catch (e) {
      const d = (e as { response?: { data?: { detail?: { problems?: string[] } | string } } })?.response?.data?.detail
      setNotice(typeof d === 'string' ? d : d?.problems?.join(' ') ?? 'Could not save that condition.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-panel" data-testid="logic-map">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold text-slate-900">Logic map<InfoTip id="logic_map" /></h2>
        <p className="text-sm text-slate-600">
          {proof === null ? 'The solver could not check this questionnaire.'
            : unreachable.size === 0 ? `Solver: all ${questions.length} questions can be reached.`
              : `Solver: ${unreachable.size} question${unreachable.size === 1 ? '' : 's'} can never be shown.`}
        </p>
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600" aria-label="Line types">
        {(Object.keys(STYLE) as Kind[]).map((k) => (
          <li key={k} className="flex items-center gap-1.5">
            <svg width="22" height="6" aria-hidden><line x1="0" y1="3" x2="22" y2="3" strokeWidth="2.5" strokeDasharray={STYLE[k].dash} className={STYLE[k].stroke} /></svg>
            {STYLE[k].label}
          </li>
        ))}
      </ul>

      {from && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-slate-800" role="status">
          {to ? <>Choose which answers to <b className="font-mono">{from}</b> show <b className="font-mono">{to}</b>.</>
            : <>Now pick a later question to show only to people who answered <b className="font-mono">{from}</b> a certain way.</>}
          <button type="button" className="ml-auto text-primary-700 underline" onClick={resetConnect}>Cancel</button>
        </div>
      )}
      {notice && <p className="mt-3 text-sm text-slate-700" role="status">{notice}</p>}

      <div className="mt-3 grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="max-h-[70vh] overflow-auto rounded-md border border-slate-100">
          <svg width="100%" height={height} role="group" aria-label="Questionnaire logic: questions in order and the conditions between them" className="min-w-[420px]">
            <defs>
              {(Object.keys(STYLE) as Kind[]).map((k) => (
                <marker key={k} id={`arrow-${k}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0,0 L8,4 L0,8 z" className={`${STYLE[k].stroke.replace('stroke-', 'fill-')}`} />
                </marker>
              ))}
            </defs>
            {edges.map((e, i) => {
              const x = nodeX - 14 - laneOf.get(e)! * LANE
              const y1 = y(e.from), y2 = y(e.to)
              const hot = selected === e.from || selected === e.to
              return (
                <path key={i} data-testid={`edge-${e.kind}`} fill="none" strokeWidth={hot ? 2.6 : 1.6}
                  strokeOpacity={selected && !hot ? 0.25 : 0.9} strokeDasharray={STYLE[e.kind].dash}
                  className={STYLE[e.kind].stroke} markerEnd={`url(#arrow-${e.kind})`}
                  d={`M${nodeX - 9},${y1} H${x + 4} Q${x},${y1} ${x},${y1 + Math.sign(y2 - y1) * 4} V${y2 - Math.sign(y2 - y1) * 4} Q${x},${y2} ${x + 4},${y2} H${nodeX - 10}`} />
              )
            })}
            {questions.map((q) => {
              const cy = y(q.qid)
              const dead = unreachable.has(q.qid)
              const later = from !== null && pos.get(q.qid)! > pos.get(from)!
              const dim = from !== null && !later && q.qid !== from
              const on = selected === q.qid || from === q.qid
              const screens = !!q.terminate_if
              return (
                <g key={q.qid} data-testid="map-node" data-unreachable={dead || undefined} role="button" tabIndex={dim ? -1 : 0}
                  aria-label={`${q.qid}: ${q.text}${dead ? '. Can never be shown' : ''}`} aria-pressed={on}
                  opacity={dim ? 0.35 : 1} className="cursor-pointer outline-none focus-visible:[&>circle]:stroke-primary-500"
                  onClick={() => !dim && pick(q.qid)}
                  onKeyDown={(ev) => { if ((ev.key === 'Enter' || ev.key === ' ') && !dim) { ev.preventDefault(); pick(q.qid) } }}>
                  <title>{q.text}</title>
                  {on && <rect x={nodeX - 14} y={cy - ROW / 2 + 3} width="100%" height={ROW - 6} rx="6" className="fill-primary-50" />}
                  <circle cx={nodeX} cy={cy} r={8} strokeWidth={2} strokeDasharray={dead ? '3 2' : undefined}
                    className={`${on ? 'fill-primary-500' : later ? 'fill-primary-100' : 'fill-white'} ${dead ? 'stroke-red-600' : screens ? 'stroke-amber-600' : 'stroke-slate-500'}`} />
                  <text x={nodeX + 16} y={cy - 3} fontSize="12" className="fill-slate-900 font-mono font-semibold">{q.qid}</text>
                  <text x={nodeX + 16} y={cy + 12} fontSize="11" className={dead ? 'fill-red-700' : 'fill-slate-500'}>
                    {dead ? 'can never be shown' : q.text.length > 70 ? `${q.text.slice(0, 69)}…` : q.text}
                  </text>
                </g>
              )
            })}
          </svg>
        </div>

        <aside className="text-sm text-slate-700 lg:sticky lg:top-4 lg:self-start" aria-live="polite" data-testid="map-panel">
          {source && target ? (
            <div className="space-y-3 rounded-md border border-slate-200 p-3">
              <p className="font-medium text-slate-900">Show <span className="font-mono">{target.qid}</span> only to people whose answer to <span className="font-mono">{source.qid}</span>…</p>
              {source.options.length ? (
                <fieldset className="space-y-1">
                  <legend className="text-xs text-slate-500">is any of</legend>
                  {source.options.map((o) => (
                    <label key={o.code} className="flex items-center gap-2">
                      <input type="checkbox" aria-label={o.label} checked={codes.includes(o.code)}
                        onChange={(ev) => setCodes(ev.target.checked ? [...codes, o.code] : codes.filter((c) => c !== o.code))} />
                      {o.label} <span className="text-xs text-slate-400">({o.code})</span>
                    </label>
                  ))}
                </fieldset>
              ) : (
                <div className="flex items-center gap-2">
                  <label className="sr-only" htmlFor="map-op">Comparison</label>
                  <select id="map-op" className="rounded border border-slate-300 px-2 py-1" value={numOp} onChange={(ev) => setNumOp(ev.target.value as 'gt' | 'lt' | 'eq')}>
                    <option value="gt">is more than</option><option value="lt">is less than</option><option value="eq">is</option>
                  </select>
                  <label className="sr-only" htmlFor="map-num">Number</label>
                  <input id="map-num" type="number" className="w-24 rounded border border-slate-300 px-2 py-1" value={num} onChange={(ev) => setNum(ev.target.value)} />
                </div>
              )}
              {target.show_if && (
                <fieldset className="space-y-1 text-xs">
                  <legend className="text-slate-500">{target.qid} already has a condition: {sentence(target.show_if, questions)}</legend>
                  <label className="flex items-center gap-2"><input type="radio" checked={mode === 'add'} onChange={() => setMode('add')} /> Keep it, and add this one (both must be true)</label>
                  <label className="flex items-center gap-2"><input type="radio" checked={mode === 'replace'} onChange={() => setMode('replace')} /> Replace it with this one</label>
                </fieldset>
              )}
              <div className="flex gap-2">
                <button type="button" disabled={!pending || saving || !canWrite}
                  className="rounded-md bg-primary-500 px-3 py-1.5 font-medium text-white hover:bg-primary-600 disabled:opacity-40"
                  onClick={() => pending && save(target.qid, combine(target.show_if, pending, mode), `Connected: ${target.qid} is now shown only if ${sentence(pending, questions)}.`)}>
                  {saving ? 'Saving…' : 'Save condition'}
                </button>
                <button type="button" className="rounded-md border border-slate-300 px-3 py-1.5" onClick={resetConnect}>Cancel</button>
              </div>
            </div>
          ) : sel ? (
            <div className="space-y-2 rounded-md border border-slate-200 p-3">
              <p className="font-mono text-xs font-semibold text-slate-900">{sel.qid} · {sel.type}</p>
              <p className="text-slate-800">{sel.text}</p>
              {unreachable.has(sel.qid) && <p className="rounded bg-red-50 px-2 py-1 text-red-800">Can never be shown: {unreachable.get(sel.qid)}</p>}
              {unproved.has(sel.qid) && <p className="text-xs text-slate-500">Not proved: {unproved.get(sel.qid)}</p>}
              <p><span className="font-medium">Shown to:</span> {sel.show_if ? `people where ${sentence(sel.show_if, questions)}` : 'everyone who gets this far'}</p>
              {sel.terminate_if && <p><span className="font-medium">Screens out:</span> people where {sentence(sel.terminate_if, questions)}</p>}
              {edges.filter((e) => e.to === sel.qid && (e.kind === 'masks' || e.kind === 'pipes')).map((e, i) => (
                <p key={i} className="text-xs text-slate-600">{STYLE[e.kind].label} {e.from}</p>
              ))}
              {canWrite && (
                <div className="flex flex-wrap gap-2 pt-1">
                  {connectable(sel) && (pos.get(sel.qid)! < questions.length - 1) && (
                    <button type="button" className="rounded-md border border-primary-300 px-2.5 py-1 text-primary-700 hover:bg-primary-50"
                      onClick={() => { setFrom(sel.qid); setTo(null); setNotice(null) }}>Connect a condition from here</button>
                  )}
                  {sel.show_if && !confirmRemove && (
                    <button type="button" className="rounded-md border border-slate-300 px-2.5 py-1" onClick={() => setConfirmRemove(true)}>Remove its condition</button>
                  )}
                  {sel.show_if && confirmRemove && (
                    <span className="flex flex-wrap items-center gap-2" role="group" aria-label="Confirm removal">
                      Show {sel.qid} to everyone who gets this far?
                      <button type="button" disabled={saving} className="rounded-md bg-red-600 px-2.5 py-1 text-white"
                        onClick={() => save(sel.qid, null, `${sel.qid} is now shown to everyone who gets this far.`)}>Yes, remove</button>
                      <button type="button" className="underline" onClick={() => setConfirmRemove(false)}>Cancel</button>
                    </span>
                  )}
                </div>
              )}
            </div>
          ) : (
            <p className="text-slate-500">Select a question to see what it depends on. {canWrite && 'To add a condition, select the question it depends on and choose “Connect a condition from here”.'}</p>
          )}
        </aside>
      </div>
    </section>
  )
}
