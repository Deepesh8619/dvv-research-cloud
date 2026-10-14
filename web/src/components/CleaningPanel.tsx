import { useCallback, useEffect, useState } from 'react'

import { CleaningState, cleaningApi } from '../api/client'
import { canWrite, isOwner } from '../auth/roles'
import Badge from './ui/Badge'
import { Button } from './ui/Button'
import { Card } from './ui/Card'

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
      {REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
    </select>
  )
}

function Row({ k, flags, decision, act, busy, onKeep, onRemove, checked, onCheck }: {
  k: string
  flags: Array<{ check: string; detail: string }>
  decision?: CleaningState['decisions'][string]
  act: boolean
  busy: boolean
  onKeep: (reason: string, note: string) => void
  onRemove: (reason: string, note: string) => void
  checked: boolean
  onCheck: (v: boolean) => void
}) {
  const [reason, setReason] = useState('other')
  const [note, setNote] = useState('')
  return (
    <li className="py-3 space-y-2 min-w-0">
      <div className="flex flex-wrap items-center gap-2">
        {act && !decision && (
          <input type="checkbox" aria-label={`select ${k}`} checked={checked} onChange={(e) => onCheck(e.target.checked)} />
        )}
        <span className="font-mono text-sm break-all">{k}</span>
        <Badge tone="warning">{flags.length} flag{flags.length === 1 ? '' : 's'}</Badge>
        {decision ? (
          <Badge tone={decision.decision === 'remove' ? 'danger' : 'success'}>
            {decision.decision}{decision.reason_code ? ` · ${decision.reason_code}` : ''}
          </Badge>
        ) : (
          <Badge>unreviewed</Badge>
        )}
      </div>
      <ul className="text-sm text-slate-700 space-y-0.5">
        {flags.map((f, i) => (
          <li key={i}><span className="font-medium">{f.check}</span>: {f.detail}</li>
        ))}
      </ul>
      {act && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <ReasonSelect label={`reason for ${k}`} value={reason} onChange={setReason} />
          <input className={input} placeholder="Note" aria-label={`note for ${k}`} value={note} onChange={(e) => setNote(e.target.value)} />
          <Button variant="secondary" disabled={busy} onClick={() => onKeep(reason, note)}>Keep</Button>
          <Button variant="danger" disabled={busy} onClick={() => onRemove(reason, note)}>Remove</Button>
        </div>
      )}
    </li>
  )
}

export default function CleaningPanel({ studyId, role }: { studyId: string; role?: string | null }) {
  const act = canWrite(role)
  const [s, setS] = useState<CleaningState | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [bulkReason, setBulkReason] = useState('other')
  const [bulkNote, setBulkNote] = useState('')
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
      setSel(new Set())
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

  const keys = Object.keys(s.flags).sort((a, b) => s.flags[b].length - s.flags[a].length)
  const decide = (ks: string[], decision: 'remove' | 'keep', reason_code: string, note: string) =>
    run(() => cleaningApi.decide(studyId, { keys: ks, decision, reason_code, note: note || undefined }))
  const unreviewed = s.n_unreviewed_flagged
  const open = !s.locked && act

  const impBody = () => ({
    variable: imp.variable, method: imp.method, target: imp.target,
    ...(imp.donor_class ? { donor_class: imp.donor_class } : {}),
    ...(imp.min !== '' ? { min: Number(imp.min) } : {}),
    ...(imp.max !== '' ? { max: Number(imp.max) } : {}),
  })

  return (
    <div className="space-y-4 min-w-0 overflow-x-hidden">
      <Card>
        <div className="flex flex-wrap gap-2 items-center">
          <Badge tone="accent">{keys.length} flagged</Badge>
          <Badge tone={unreviewed ? 'warning' : 'success'}>{unreviewed} unreviewed</Badge>
          <Badge tone={s.locked ? 'info' : 'neutral'}>{s.locked ? 'Locked' : 'Unlocked'}</Badge>
          <Badge>version {s.versions.length || 'none yet'}</Badge>
        </div>
        <Err text={err} />
      </Card>

      <Card>
        <h3 className="font-semibold text-slate-900 mb-2">Flagged respondents</h3>
        {keys.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing flagged.</p>
        ) : (
          <>
            {open && sel.size > 0 && (
              <div className="mb-3 flex flex-col gap-2 rounded-md bg-slate-50 p-3 sm:flex-row sm:items-center">
                <span className="text-sm">{sel.size} selected</span>
                <ReasonSelect label="bulk reason" value={bulkReason} onChange={setBulkReason} />
                <input className={input} placeholder="Note" aria-label="bulk note" value={bulkNote} onChange={(e) => setBulkNote(e.target.value)} />
                <Button variant="danger" disabled={busy} onClick={() => decide([...sel], 'remove', bulkReason, bulkNote)}>
                  Remove selected
                </Button>
              </div>
            )}
            <ul className="divide-y divide-slate-100">
              {keys.map((k) => (
                <Row
                  key={k} k={k} flags={s.flags[k]} decision={s.decisions[k]} act={open} busy={busy}
                  checked={sel.has(k)}
                  onCheck={(v) => setSel((p) => { const n = new Set(p); if (v) n.add(k); else n.delete(k); return n })}
                  onKeep={(r, n) => decide([k], 'keep', r, n)}
                  onRemove={(r, n) => decide([k], 'remove', r, n)}
                />
              ))}
            </ul>
          </>
        )}
      </Card>

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

      {act && (
        <Card>
          {!s.locked ? (
            <Button disabled={busy || unreviewed > 0} onClick={() => run(() => cleaningApi.lock(studyId))}>
              {unreviewed > 0 ? `Lock cleaning (${unreviewed} unreviewed)` : 'Lock cleaning'}
            </Button>
          ) : isOwner(role) ? (
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
