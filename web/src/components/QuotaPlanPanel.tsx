import { useEffect, useState } from 'react'

import { questionnaireApi, type Question, type QuotaPlanCell } from '../api/client'

/**
 * Quotas as the researcher thinks of them: shares of the completes the study
 * needs — "Women 50%", "18–34 30%". Saved with the questionnaire; every real
 * link opened afterwards gets these cells, each limited to its share.
 */
const input = 'h-8 rounded-md border border-slate-300 px-2 text-sm'

export default function QuotaPlanPanel({ studyId, questions }: { studyId: string; questions: Question[] }) {
  const choice = questions.filter((q) => q.type === 'single' || q.type === 'dropdown')
  const [open, setOpen] = useState(false)
  const [completes, setCompletes] = useState('')
  const [cells, setCells] = useState<QuotaPlanCell[]>([])
  const [saved, setSaved] = useState<Array<{ name: string; limit: number }>>([])
  const [errors, setErrors] = useState<string[]>([])

  useEffect(() => {
    questionnaireApi.quotaPlan(studyId).then((r) => {
      setCompletes(r.data.completes ? String(r.data.completes) : '')
      setCells(r.data.cells)
      setSaved(r.data.limits)
    }).catch(() => undefined)
  }, [studyId])

  const set = (i: number, patch: Partial<QuotaPlanCell>) => setCells(cells.map((c, j) => (j === i ? { ...c, ...patch } : c)))
  const totals: Record<string, number> = {}
  for (const c of cells) totals[c.qid] = (totals[c.qid] ?? 0) + (Number(c.share_pct) || 0)
  const n = Number(completes)

  const save = async () => {
    setErrors([])
    try {
      const r = await questionnaireApi.saveQuotaPlan(studyId, { completes: n, cells })
      setSaved(r.data.limits)
    } catch (e) {
      const d = (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
      setErrors(Array.isArray(d) ? d.map(String) : ['Could not save the quotas.'])
    }
  }

  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="rounded-full border border-slate-300 px-3 py-1 text-xs font-medium text-slate-600">
        Quotas{saved.length ? ` · ${saved.length}` : ''}
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-20 w-[34rem] max-w-[92vw] space-y-3 rounded-lg border border-slate-200 bg-white p-4 shadow-lg">
          <p className="text-sm text-slate-600">
            Shares of the completes you need. Links opened from now on fill each quota up to its share, then turn
            further people in it away.
          </p>
          <label className="block text-xs font-medium text-slate-600">Completes needed
            <input className={`${input} ml-2 w-24`} inputMode="numeric" value={completes}
              onChange={(e) => setCompletes(e.target.value.replace(/\D/g, ''))} />
          </label>
          {cells.map((c, i) => {
            const q = choice.find((x) => x.qid === c.qid)
            const over = (totals[c.qid] ?? 0) > 100
            return (
              <div key={i} className="space-y-2 rounded-md bg-slate-50 p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <input className={`${input} w-32`} placeholder="Name, e.g. Women" aria-label="quota name" value={c.name}
                    onChange={(e) => set(i, { name: e.target.value })} />
                  <select className={`${input} min-w-0 flex-1`} aria-label="quota question" value={c.qid}
                    onChange={(e) => set(i, { qid: e.target.value, codes: [] })}>
                    <option value="">question…</option>
                    {choice.map((x) => <option key={x.qid} value={x.qid}>{x.qid} — {x.text.slice(0, 50)}</option>)}
                  </select>
                  <input className={`${input} w-16`} inputMode="decimal" aria-label="quota share" value={c.share_pct || ''}
                    onChange={(e) => set(i, { share_pct: Number(e.target.value) || 0 })} />
                  <span className="text-sm text-slate-600">%</span>
                  {n > 0 && c.share_pct > 0 && <span className="text-xs text-slate-500">= {Math.ceil((n * c.share_pct) / 100)}</span>}
                  <button type="button" aria-label="remove quota" className="ml-auto text-slate-400 hover:text-red-600"
                    onClick={() => setCells(cells.filter((_, j) => j !== i))}>✕</button>
                </div>
                {q && (
                  <div className="flex flex-wrap gap-1" role="group" aria-label="quota answers">
                    {q.options.map((o) => {
                      const on = c.codes.includes(o.code)
                      return (
                        <button key={o.code} type="button" aria-pressed={on}
                          onClick={() => set(i, { codes: on ? c.codes.filter((x) => x !== o.code) : [...c.codes, o.code] })}
                          className={`rounded-full border px-2.5 py-0.5 text-xs ${on ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300 bg-white text-slate-700'}`}>
                          {o.label}
                        </button>
                      )
                    })}
                  </div>
                )}
                {over && <p className="text-xs text-red-700">{c.qid}: shares add up to {totals[c.qid]}%.</p>}
              </div>
            )
          })}
          <div className="flex items-center gap-2">
            <button type="button" className="text-sm font-medium text-primary-600"
              onClick={() => setCells([...cells, { name: '', qid: '', codes: [], share_pct: 0 }])}>+ Add quota</button>
            <button type="button" onClick={save} disabled={!n}
              className="ml-auto rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">Save quotas</button>
          </div>
          {errors.length > 0 && <ul className="space-y-0.5 text-sm text-red-700">{errors.map((e) => <li key={e}>{e}</li>)}</ul>}
          {saved.length > 0 && errors.length === 0 && (
            <p className="text-sm text-emerald-700">Saved: {saved.map((s) => `${s.name} ${s.limit}`).join(' · ')}.</p>
          )}
        </div>
      )}
    </div>
  )
}
