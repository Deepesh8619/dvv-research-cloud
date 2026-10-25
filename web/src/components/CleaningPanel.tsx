import { useCallback, useEffect, useState } from 'react'

import { CleaningEvidence, CleaningState, cleaningApi } from '../api/client'
import { canWrite, isOwner } from '../auth/roles'
import Badge from './ui/Badge'
import { Button } from './ui/Button'
import { Card } from './ui/Card'
import InfoTip from './ui/InfoTip'

// Mirrors src/engines/qc/cleaning.py REASON_CODES.
const REASONS = [
  'speeder', 'straightliner', 'duplicate', 'gibberish', 'trap_failed', 'red_herring',
  'inconsistent', 'fraud_signal', 'out_of_range', 'erasure', 'client_request', 'other',
]

/** The server's message verbatim: a string, or a 422 problem list. */
function msg(err: unknown): string {
  const d = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
  if (Array.isArray(d)) return d.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join('\n')
  if (typeof d === 'string') return d
  return err instanceof Error ? err.message : 'Request failed'
}

const input = 'w-full min-h-[44px] rounded-md border border-slate-300 px-3 py-2 text-sm'

function Err({ text }: { text: string }) {
  return text ? (
    <p role="alert" className="whitespace-pre-wrap break-words text-sm text-red-700">{text}</p>
  ) : null
}

function ReasonSelect({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  return (
    <select aria-label={label} className={input} value={value} onChange={(e) => onChange(e.target.value)}>
      {REASONS.map((r) => <option key={r} value={r}>{REASON_LABEL[r] ?? r}</option>)}
    </select>
  )
}

type Flag = { check: string; detail: string }
type Band = 'review first' | 'worth a look' | 'fine'

const REASON_LABEL: Record<string, string> = {
  speeder: 'Too fast', straightliner: 'Straight-lined', duplicate: 'Duplicate', gibberish: 'Gibberish',
  trap_failed: 'Failed a trap question', red_herring: 'Picked a fake brand', inconsistent: 'Contradicts themselves',
  fraud_signal: 'Fraud signal', out_of_range: 'Out of range', erasure: 'Asked to be erased',
  client_request: 'Client asked', other: 'Other (say why)',
}
const CHECK_LABEL: Record<string, string> = {
  speeder: 'Too fast', too_fast_to_read: 'Faster than reading', straightliner: 'Straight-lined',
  duplicate: 'Duplicate', gibberish: 'Gibberish', pasted_answer: 'Pasted answer', ai_text: 'AI-written',
  few_keystrokes: 'Few keystrokes', trap_failed: 'Failed a trap', fraud_signal: 'Fraud signal', inconsistent: 'Inconsistent',
  person_fit: "Answers don't fit the pattern",
}
// The reason a reviewer would pick for the strongest flag; "other" needs a note.
const REASON_FOR: Record<string, string> = {
  speeder: 'speeder', too_fast_to_read: 'speeder', straightliner: 'straightliner', duplicate: 'duplicate',
  gibberish: 'gibberish', pasted_answer: 'fraud_signal', ai_text: 'fraud_signal', few_keystrokes: 'fraud_signal',
  fraud_signal: 'fraud_signal', trap_failed: 'trap_failed', inconsistent: 'inconsistent',
  person_fit: 'fraud_signal',
}
const BAND_DOT: Record<Band, string> = { 'review first': 'bg-red-500', 'worth a look': 'bg-amber-400', fine: 'bg-slate-300' }

function grouped(flags: Flag[]): Array<{ check: string; n: number }> {
  const out: Record<string, number> = {}
  for (const f of flags) out[f.check] = (out[f.check] ?? 0) + 1
  return Object.entries(out).map(([check, n]) => ({ check, n })).sort((a, b) => b.n - a.n)
}

/** "Remove all N", then a second, explicit step: many respondents at once is
 *  not a click to make by accident. Each stays changeable until cleaning is locked. */
function ConfirmRemove({ n, busy, onConfirm }: { n: number; busy: boolean; onConfirm: () => void }) {
  const [asking, setAsking] = useState(false)
  if (!asking) return <Button variant="danger" disabled={busy} onClick={() => setAsking(true)}>Remove all {n}</Button>
  return (
    <span className="flex flex-wrap items-center gap-2 text-sm text-slate-800" role="group" aria-label="Confirm removal">
      Remove {n} respondent{n === 1 ? '' : 's'}? You can change each one until cleaning is locked.
      <Button variant="danger" disabled={busy} onClick={() => { setAsking(false); onConfirm() }}>Yes, remove {n}</Button>
      <Button variant="secondary" onClick={() => setAsking(false)}>Cancel</Button>
    </span>
  )
}

/** Authenticity pill, signals in words, and total time against the median.
 *  Exports carry no per-question times, so the strip is total vs. median. */
function Authenticity({ ev }: { ev: CleaningEvidence }) {
  const a = ev.authenticity
  if (!a) return null
  // The same score and band the list ranks by: 0 is no concern, 100 the most.
  const tone = a.band === 'review first' ? 'bg-red-50 text-red-800'
    : a.band === 'worth a look' ? 'bg-amber-50 text-amber-900' : 'bg-emerald-50 text-emerald-800'
  const max = Math.max(ev.seconds ?? 0, ev.median_seconds ?? 0)
  const pct = (v: number) => `${Math.max(2, Math.round((v / max) * 100))}%`
  return (
    <div className="space-y-2 rounded-md border border-slate-200 p-3">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className={`rounded-full px-2.5 py-0.5 font-semibold ${tone}`}>Concern {a.score} / 100</span>
        <span className="text-slate-600">{a.band}</span>
        <InfoTip id="cleaning_authenticity" />
      </p>
      {a.signals.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-700">
          {a.signals.map((x, i) => (
            <li key={`${x.key}-${i}`}><span className="font-medium">{x.label}</span> (+{x.weight}){x.detail ? ` — ${x.detail}` : ''}</li>))}
        </ul>
      ) : <p className="text-sm text-slate-600">No warning signs found.</p>}
      {ev.seconds != null && ev.median_seconds != null && max > 0 && (
        <div className="space-y-1 text-xs text-slate-600" role="img"
          aria-label={`Took ${Math.round(ev.seconds / 60)} minutes against a median of ${Math.round(ev.median_seconds / 60)}`}>
          <div className="flex items-center gap-2"><span className="w-14">This one</span>
            <span className="h-2 rounded bg-primary-600" style={{ width: pct(ev.seconds) }} /></div>
          <div className="flex items-center gap-2"><span className="w-14">Median</span>
            <span className="h-2 rounded bg-slate-400" style={{ width: pct(ev.median_seconds) }} /></div>
        </div>
      )}
      {(ev.paradata?.pasted.length ?? 0) > 0 && (
        <p className="text-xs font-medium text-red-800">⚠ Pasted: {ev.paradata!.pasted.join(', ')}</p>
      )}
    </div>
  )
}

function Evidence({ studyId, k, flags, decision, act, busy, onDecide }: {
  studyId: string
  k: string
  flags: Flag[]
  decision?: CleaningState['decisions'][string]
  act: boolean
  busy: boolean
  onDecide: (decision: 'keep' | 'remove', reason: string, note: string) => void
}) {
  const [ev, setEv] = useState<CleaningEvidence | null>(null)
  const [evErr, setEvErr] = useState('')
  const strongest = grouped(flags)[0]?.check
  const [reason, setReason] = useState(REASON_FOR[strongest] ?? 'other')
  const [note, setNote] = useState('')
  useEffect(() => {
    setEv(null); setEvErr(''); setNote(''); setReason(REASON_FOR[grouped(flags)[0]?.check] ?? 'other')
    cleaningApi.evidence(studyId, k, flags).then((r) => setEv(r.data)).catch((e) => setEvErr(msg(e)))
  }, [studyId, k, flags])
  const needsNote = reason === 'other' && !note.trim()

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="font-mono text-base font-semibold text-slate-900 break-all">{k}</h4>
        {ev?.seconds != null && (
          <p className="text-sm text-slate-600">
            Took {Math.round(ev.seconds / 60)} min{ev.median_seconds ? ` · most took ${Math.round(ev.median_seconds / 60)} min` : ''}
          </p>
        )}
      </div>
      {decision && (
        <p className={`rounded-md px-3 py-2 text-sm ${decision.decision === 'remove' ? 'bg-red-50 text-red-800' : 'bg-emerald-50 text-emerald-800'}`}>
          {decision.decision === 'remove' ? 'Removed' : 'Kept'}{decision.reason_code ? ` — ${REASON_LABEL[decision.reason_code] ?? decision.reason_code}` : ''}
          {decision.note ? `: ${decision.note}` : ''}. Deciding again replaces this.
        </p>
      )}
      <Err text={evErr} />
      {ev && <Authenticity ev={ev} />}
      <ul className="space-y-3">
        {(ev?.items ?? flags.map((f) => ({ ...f, answers: [] }))).map((it, i) => (
          <li key={i} className="rounded-md border border-slate-200 p-3">
            <p className="text-sm font-medium text-slate-900">
              {CHECK_LABEL[it.check] ?? it.check}
              {it.answers.length === 0 && <span className="font-normal text-slate-600"> — {it.detail}</span>}
            </p>
            {it.answers.map((a) => (
              <div key={a.column} className="mt-2">
                <p className="text-xs text-slate-500"><span className="font-mono">{a.column}</span> · {a.question}</p>
                <p className="mt-0.5 whitespace-pre-wrap break-words text-sm text-slate-800">{a.answer || <em className="text-slate-400">blank</em>}</p>
              </div>
            ))}
          </li>
        ))}
      </ul>
      {act && (
        <div className="space-y-2 border-t border-slate-100 pt-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <ReasonSelect label={`reason for ${k}`} value={reason} onChange={setReason} />
            <input className={input} placeholder={reason === 'other' ? 'Why? (required)' : 'Note (optional)'}
              aria-label={`note for ${k}`} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" disabled={busy || needsNote} onClick={() => onDecide('keep', reason, note)}>Keep</Button>
            <Button variant="danger" disabled={busy || needsNote} onClick={() => onDecide('remove', reason, note)}>Remove</Button>
            <span className="text-xs text-slate-500">Keys: ↑ ↓ move · K keep · R remove</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function CleaningPanel({ studyId, role }: { studyId: string; role?: string | null }) {
  const act = canWrite(role)
  const [s, setS] = useState<CleaningState | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [active, setActive] = useState<string | null>(null)
  const [band, setBand] = useState<'all' | Band>('all')
  const [check, setCheck] = useState<string | null>(null)
  const [showReviewed, setShowReviewed] = useState(false)
  // rules
  const [rulesText, setRulesText] = useState('[]')
  const [proposed, setProposed] = useState<Array<Record<string, unknown>>>([])
  const [rulesMsg, setRulesMsg] = useState('')
  const [rulesErr, setRulesErr] = useState('')
  // imputation
  const [imp, setImp] = useState({ variable: '', method: 'median', target: 'missing', donor_class: '', min: '', max: '' })
  const [impPlan, setImpPlan] = useState<{ n_cells: number; cells?: Array<Record<string, unknown>> } | null>(null)
  const [impMsg, setImpMsg] = useState('')
  const [impErr, setImpErr] = useState('')
  // unlock
  const [unlockNote, setUnlockNote] = useState('')

  const load = useCallback(async () => {
    try {
      const [c, r] = await Promise.all([cleaningApi.get(studyId), cleaningApi.rules(studyId)])
      setS(c.data)
      setRulesText(JSON.stringify(r.data.rules, null, 2))
      setProposed(r.data.proposed ?? [])
      setLoadErr('')
    } catch (e) {
      setLoadErr(msg(e))
    }
  }, [studyId])
  useEffect(() => { void load() }, [load])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    setErr('')
    try {
      await fn()
      await load()
    } catch (e) {
      setErr(msg(e))
    } finally {
      setBusy(false)
    }
  }

  if (loadErr) return <Err text={loadErr} />
  if (!s) return <p className="text-sm text-slate-500">Loading cleaning review…</p>

  // Highest authenticity concern first: the order a reviewer should work in.
  const scoreOf = (k: string) => s.authenticity?.[k]?.score ?? s.flags[k].length
  const bandOf = (k: string): Band => s.authenticity?.[k]?.band ?? 'fine'
  const all = Object.keys(s.flags).sort((x, y) => scoreOf(y) - scoreOf(x))
  const checks = grouped(all.flatMap((k) => [...new Set(s.flags[k].map((f) => f.check))].map((check) => ({ check, detail: '' }))))
  const keys = all.filter((k) =>
    (band === 'all' || bandOf(k) === band) &&
    (!check || s.flags[k].some((f) => f.check === check)) &&
    (showReviewed || !s.decisions[k]))
  const current = active && keys.includes(active) ? active : keys[0]
  const removed = all.filter((k) => s.decisions[k]?.decision === 'remove').length
  const kept = all.filter((k) => s.decisions[k]?.decision === 'keep').length
  const unreviewed = s.n_unreviewed_flagged
  const open = !s.locked && act
  const done = all.length ? Math.round(((all.length - unreviewed) / all.length) * 100) : 100

  const decide = async (ks: string[], decision: 'remove' | 'keep', reason_code: string, note: string) => {
    // Move on to the next one still to review, as a reviewer would.
    const next = keys.find((k) => !ks.includes(k) && !s.decisions[k] && keys.indexOf(k) > keys.indexOf(ks[0])) ??
      keys.find((k) => !ks.includes(k) && !s.decisions[k])
    await run(() => cleaningApi.decide(studyId, { keys: ks, decision, reason_code, note: note || undefined }))
    if (next) setActive(next)
  }
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).closest('input, select, textarea')) return
    const i = keys.indexOf(current ?? '')
    if (e.key === 'ArrowDown' && i < keys.length - 1) { e.preventDefault(); setActive(keys[i + 1]) }
    if (e.key === 'ArrowUp' && i > 0) { e.preventDefault(); setActive(keys[i - 1]) }
    if (!open || !current || busy) return
    const reason = REASON_FOR[grouped(s.flags[current])[0]?.check] ?? 'other'
    if (reason === 'other') return  // "other" needs a note: use the buttons
    if (e.key.toLowerCase() === 'k') void decide([current], 'keep', reason, '')
    if (e.key.toLowerCase() === 'r') void decide([current], 'remove', reason, '')
  }
  const reviewFirst = all.filter((k) => bandOf(k) === 'review first' && !s.decisions[k])

  const impBody = () => ({
    variable: imp.variable, method: imp.method, target: imp.target,
    ...(imp.donor_class ? { donor_class: imp.donor_class } : {}),
    ...(imp.min !== '' ? { min: Number(imp.min) } : {}),
    ...(imp.max !== '' ? { max: Number(imp.max) } : {}),
  })
  const chip = (on: boolean) =>
    `rounded-full border px-3 py-1 text-sm ${on ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300 text-slate-700 hover:bg-slate-50'}`

  return (
    <div className="space-y-4 min-w-0 overflow-x-hidden">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <p className="text-sm text-slate-700">
              <span className="text-lg font-semibold text-slate-900">{unreviewed}</span> of {all.length} flagged respondents to review<InfoTip id="cleaning" />
              <span className="text-slate-500"> · {removed} removed · {kept} kept</span>
            </p>
            <div className="mt-2 h-1.5 w-full max-w-md rounded bg-slate-100" aria-hidden>
              <div className="h-1.5 rounded bg-emerald-500" style={{ width: `${done}%` }} />
            </div>
          </div>
          {act && !s.locked && (
            <span className="inline-flex items-center">
              <Button disabled={busy || unreviewed > 0} onClick={() => run(() => cleaningApi.lock(studyId))}>
                {unreviewed > 0 ? `Lock cleaning (${unreviewed} unreviewed)` : 'Lock cleaning'}
              </Button>
              <InfoTip id="cleaning_lock" align="right" />
            </span>
          )}
          {s.locked && <Badge tone="info">Locked · version {s.versions.length}</Badge>}
        </div>
        <Err text={err} />
      </Card>

      {open && (s.suggestions ?? []).map((g) => {
        const name = (CHECK_LABEL[g.check] ?? g.label).toLowerCase()
        return (
          <Card key={g.check}>
            <p className="text-sm text-slate-800">
              On {g.studies} past studies you removed {g.removed} of {g.decided} respondents flagged <em>{name}</em>.{' '}
              {g.keys.length} here {g.keys.length === 1 ? 'is' : 'are'} flagged the same way.
              <InfoTip id="cleaning_habits" />
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button variant="secondary" disabled={busy}
                onClick={() => { setBand('all'); setCheck(g.check); setShowReviewed(false) }}>Review them</Button>
              <ConfirmRemove n={g.keys.length} busy={busy}
                onConfirm={() => decide(g.keys, 'remove', 'other', `Removed together: you usually remove “${name}” (${g.removed} of ${g.decided} on ${g.studies} past studies)`)} />
            </div>
          </Card>
        )
      })}

      {all.length === 0 ? (
        <Card><p className="text-sm text-slate-600">No respondent was flagged. Nothing to review.</p></Card>
      ) : (
        <Card>
          <div className="flex flex-wrap items-center gap-2">
            {(['all', 'review first', 'worth a look', 'fine'] as const).map((b) => (
              <button key={b} type="button" className={chip(band === b)} onClick={() => setBand(b)}>
                {b === 'all' ? 'All' : b[0].toUpperCase() + b.slice(1)}
              </button>
            ))}
            <span className="mx-1 h-5 w-px bg-slate-200" aria-hidden />
            <InfoTip id="cleaning_checks" />
            {checks.map((c) => (
              <button key={c.check} type="button" className={chip(check === c.check)}
                onClick={() => setCheck(check === c.check ? null : c.check)}>
                {CHECK_LABEL[c.check] ?? c.check} {c.n}
              </button>
            ))}
            <label className="ml-auto flex items-center gap-1.5 text-sm text-slate-600">
              <input type="checkbox" checked={showReviewed} onChange={(e) => setShowReviewed(e.target.checked)} />
              Show reviewed
            </label>
          </div>
          {open && reviewFirst.length > 1 && band === 'review first' && (
            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md bg-red-50 p-3 text-sm text-red-900">
              {reviewFirst.length} still marked “review first”.
              <ConfirmRemove n={reviewFirst.length} busy={busy}
                onConfirm={() => decide(reviewFirst, 'remove', 'other', 'Removed together: all marked review first by the authenticity score')} />
            </div>
          )}
          {keys.length === 0 ? (
            <p className="mt-4 text-sm text-slate-600">Nobody here. {showReviewed ? 'Try another filter.' : 'Tick “Show reviewed” to see decided ones.'}</p>
          ) : (
            <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]" tabIndex={0} onKeyDown={onKey}
              aria-label="Flagged respondents: arrow keys move, K keeps, R removes">
              <ul aria-label="Flagged respondents" className="max-h-[36rem] divide-y divide-slate-100 overflow-y-auto rounded-md border border-slate-200">
                {keys.map((k) => {
                  const d = s.decisions[k]
                  const a = s.authenticity?.[k]
                  return (
                    <li key={k}>
                      <button type="button" onClick={() => setActive(k)}
                        className={`w-full px-3 py-2 text-left ${k === current ? 'bg-slate-100' : 'hover:bg-slate-50'}`}>
                        <span className="flex items-center gap-2">
                          <span className={`h-2 w-2 shrink-0 rounded-full ${BAND_DOT[bandOf(k)]}`} aria-hidden />
                          <span className="truncate font-mono text-sm text-slate-900">{k}</span>
                          {a && <span className="ml-auto text-xs tabular-nums text-slate-500">{a.score}</span>}
                        </span>
                        <span className="mt-0.5 block truncate pl-4 text-xs text-slate-600">
                          {d ? (d.decision === 'remove' ? 'Removed' : 'Kept') + ' · ' : ''}
                          {grouped(s.flags[k]).map((g) => `${CHECK_LABEL[g.check] ?? g.check}${g.n > 1 ? ` ×${g.n}` : ''}`).join(', ')}
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
              {current && (
                <Evidence key={current} studyId={studyId} k={current} flags={s.flags[current]}
                  decision={s.decisions[current]} act={open} busy={busy}
                  onDecide={(d, r, n) => decide([current], d, r, n)} />
              )}
            </div>
          )}
        </Card>
      )}

      <details className="rounded-lg border border-slate-200 bg-white p-4 shadow-panel">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">Rules, imputation and versions</summary>
        <div className="mt-4 space-y-4">
      <Card>
        <h3 className="font-semibold text-slate-900 mb-2">Rules</h3>
        <textarea
          aria-label="cleaning rules JSON" rows={8} readOnly={!open}
          className="w-full rounded-md border border-slate-300 p-2 font-mono text-xs"
          value={rulesText} onChange={(e) => setRulesText(e.target.value)}
        />
        <Err text={rulesErr} />
        {rulesMsg && <p className="text-sm text-emerald-700">{rulesMsg}</p>}
        {open && (
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <Button variant="primary" disabled={busy} onClick={async () => {
              setRulesErr(''); setRulesMsg('')
              let parsed: unknown
              try { parsed = JSON.parse(rulesText) } catch (e) { setRulesErr(`Not valid JSON: ${(e as Error).message}`); return }
              if (!Array.isArray(parsed)) { setRulesErr('Rules must be a JSON list'); return }
              // ponytail: the server has no dry-run; PUT validates first, then saves.
              try {
                await cleaningApi.putRules(studyId, parsed)
                setRulesMsg('Rules are valid and were saved.')
                await load()
              } catch (e) { setRulesErr(msg(e)) }
            }}>Validate and save</Button>
          </div>
        )}
        {proposed.length > 0 && (
          <div className="mt-3">
            <p className="text-sm font-medium">Proposed from the questionnaire <Badge tone="warning">not yet saved</Badge></p>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-xs">
              {JSON.stringify(proposed, null, 2)}
            </pre>
          </div>
        )}
      </Card>

      <Card>
        <h3 className="font-semibold text-slate-900 mb-2">Imputation</h3>
        {s.imputations.length > 0 && (
          <p className="mb-2 text-sm text-slate-600 break-words">
            Stored: {s.imputations.map((p) => `${p.variable} (${p.method}, ${p.target})`).join('; ')}
          </p>
        )}
        {open ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <input className={input} placeholder="Variable" aria-label="imputation variable" value={imp.variable} onChange={(e) => { setImp({ ...imp, variable: e.target.value }); setImpPlan(null) }} />
            <select className={input} aria-label="imputation method" value={imp.method} onChange={(e) => { setImp({ ...imp, method: e.target.value }); setImpPlan(null) }}>
              {['mean', 'median', 'hot_deck'].map((m) => <option key={m}>{m}</option>)}
            </select>
            <select className={input} aria-label="imputation target" value={imp.target} onChange={(e) => { setImp({ ...imp, target: e.target.value }); setImpPlan(null) }}>
              {['missing', 'out_of_range'].map((m) => <option key={m}>{m}</option>)}
            </select>
            <input className={input} placeholder="Donor class (hot_deck)" aria-label="donor class" value={imp.donor_class} onChange={(e) => setImp({ ...imp, donor_class: e.target.value })} />
            <input className={input} placeholder="Min" inputMode="decimal" aria-label="imputation min" value={imp.min} onChange={(e) => setImp({ ...imp, min: e.target.value })} />
            <input className={input} placeholder="Max" inputMode="decimal" aria-label="imputation max" value={imp.max} onChange={(e) => setImp({ ...imp, max: e.target.value })} />
            <Button variant="secondary" disabled={busy || !imp.variable} onClick={async () => {
              setImpErr(''); setImpMsg(''); setImpPlan(null)
              try { setImpPlan((await cleaningApi.imputation(studyId, impBody(), true)).data) } catch (e) { setImpErr(msg(e)) }
            }}>Preview</Button>
          </div>
        ) : (
          <p className="text-sm text-slate-500">{s.locked ? 'Locked.' : 'You can view but not change imputation.'}</p>
        )}
        <Err text={impErr} />
        {impMsg && <p className="text-sm text-emerald-700">{impMsg}</p>}
        {impPlan && (
          <div className="mt-2 space-y-2">
            <p className="text-sm">Preview: {impPlan.n_cells} cell{impPlan.n_cells === 1 ? '' : 's'} would change. Nothing has been written.</p>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-xs">
              {JSON.stringify((impPlan.cells ?? []).slice(0, 10), null, 2)}
            </pre>
            <Button variant="primary" disabled={busy} onClick={async () => {
              setImpErr('')
              try {
                await cleaningApi.imputation(studyId, { ...impBody(), confirm: true }, false)
                setImpMsg('Policy stored.'); setImpPlan(null); await load()
              } catch (e) { setImpErr(msg(e)) }
            }}>Confirm and store policy</Button>
          </div>
        )}
      </Card>

      <Card>
        <h3 className="font-semibold text-slate-900 mb-2">Versions</h3>
        {s.versions.length === 0 ? (
          <p className="text-sm text-slate-500">No locked version yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {s.versions.map((v, i) => (
              <li key={i} className="break-words">
                v{String(v.version)} · locked by {String(v.locked_by)} · {String(v.locked_at)}
              </li>
            ))}
          </ul>
        )}
      </Card>
        </div>
      </details>


      {act && s.locked && (
        <Card>
          {isOwner(role) ? (
            <div className="flex flex-col gap-2 sm:flex-row">
              <input className={input} placeholder="Why unlock? (required)" aria-label="unlock note" value={unlockNote} onChange={(e) => setUnlockNote(e.target.value)} />
              <Button variant="danger" disabled={busy || !unlockNote.trim()} onClick={() => run(() => cleaningApi.unlock(studyId, unlockNote))}>Unlock</Button>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Only the owner can unlock.</p>
          )}
        </Card>
      )}
    </div>
  )
}
