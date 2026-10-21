import { useCallback, useEffect, useState } from 'react'

import { downloadFile, fieldingApi, type SupplierRow } from '../api/client'

/**
 * Sample suppliers: one row per live link — who sends it, what they cost,
 * how far along, and how much of it cleaning threw out. The reconciliation
 * file is what each panel asks for at the end of field: every respondent id,
 * accepted or rejected (with the reason a person gave).
 */
const n = (v: number | null | undefined, suffix = '') => (v === null || v === undefined ? '—' : `${v}${suffix}`)
const field = 'w-full rounded-md border border-slate-300 px-2 py-1 text-sm tabular-nums'

function Edit({ studyId, row, onSaved }: { studyId: string; row: SupplierRow; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: row.vendor_name ?? '', cpi: row.cpi?.toString() ?? '', target: row.target_completes?.toString() ?? '',
  })
  const [error, setError] = useState<string | null>(null)
  const num = (v: string) => (v.trim() === '' ? null : Number(v))
  const save = () =>
    fieldingApi
      .setVendor(studyId, row.link_id, {
        vendor_name: form.name.trim() || null, cpi: num(form.cpi), target_completes: num(form.target),
      })
      .then(onSaved)
      .catch(() => setError('Price must be 0 or more and the target at least 1.'))
  return (
    <div className="mt-2 grid items-end gap-2 sm:grid-cols-4">
      <label className="text-xs text-slate-600">Supplier
        <input className={field} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </label>
      <label className="text-xs text-slate-600">Cost per complete
        <input className={field} inputMode="decimal" value={form.cpi}
          onChange={(e) => setForm({ ...form, cpi: e.target.value })} />
      </label>
      <label className="text-xs text-slate-600">Target completes
        <input className={field} inputMode="numeric" value={form.target}
          onChange={(e) => setForm({ ...form, target: e.target.value })} />
      </label>
      <button type="button" onClick={save}
        className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white">Save</button>
      {error && <p className="text-xs text-red-700 sm:col-span-4">{error}</p>}
    </div>
  )
}

export default function SuppliersPanel({ studyId, canWrite }: { studyId: string; canWrite: boolean }) {
  const [rows, setRows] = useState<SupplierRow[] | null>(null)
  const [editing, setEditing] = useState<string | null>(null)

  // Not polled: the scorecard runs a field report and QC per link.
  const load = useCallback(() => {
    fieldingApi.suppliers(studyId).then((r) => setRows(r.data.vendors)).catch(() => setRows(null))
  }, [studyId])
  useEffect(load, [load])

  if (!rows || rows.length === 0) return null
  return (
    <section className="mb-6 rounded-lg border border-slate-200 bg-white p-5 shadow-panel" data-testid="suppliers">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold text-slate-900">Sample suppliers</h2>
        <button type="button" onClick={load} className="text-sm text-primary-500 hover:text-primary-600">Refresh</button>
      </div>
      <p className="mt-1 max-w-2xl text-sm text-slate-500">
        Accepted completes are the ones nobody removed in cleaning — what each panel is paid for. The reconciliation
        file lists every respondent id with that decision.
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[600px] text-sm">
          <thead className="text-left text-[11px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="py-2 pr-3 font-medium">Supplier</th>
              <th className="py-2 pr-3 font-medium">Completes / target</th>
              <th className="py-2 pr-3 font-medium">Incidence</th>
              <th className="py-2 pr-3 font-medium">Removed</th>
              <th className="py-2 pr-3 font-medium">Accepted</th>
              <th className="py-2 pr-3 font-medium">Billable</th>
              <th className="py-2 font-medium" />
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map((r) => (
              <tr key={r.link_id} className="border-t border-slate-100 align-top">
                <td className="py-2 pr-3">
                  <span className="font-medium text-slate-900">{r.vendor_name || 'Unnamed link'}</span>
                  <span className="block text-xs text-slate-500">{r.cpi === null ? 'no price set' : `${r.cpi} per complete`}</span>
                  {editing === r.link_id && (
                    <Edit studyId={studyId} row={r} onSaved={() => { setEditing(null); load() }} />
                  )}
                </td>
                <td className="py-2 pr-3">{r.completes} / {n(r.target_completes)}
                  {r.pct_of_target !== null && <span className="block text-xs text-slate-500">{r.pct_of_target}%</span>}
                </td>
                <td className="py-2 pr-3">{n(r.incidence_pct, '%')}</td>
                <td className={`py-2 pr-3 ${(r.pct_removed ?? 0) >= 15 ? 'text-amber-700' : ''}`}>{n(r.pct_removed, '%')}</td>
                <td className="py-2 pr-3">{r.accepted}</td>
                <td className="py-2 pr-3">{n(r.billable)}</td>
                <td className="py-2 text-right">
                  <div className="flex flex-col items-end gap-1">
                  {canWrite && (
                    <button type="button" className="text-sm text-slate-600 hover:text-slate-900"
                      onClick={() => setEditing(editing === r.link_id ? null : r.link_id)}>
                      {editing === r.link_id ? 'Cancel' : 'Edit'}
                    </button>
                  )}
                  <button type="button" className="text-sm font-medium text-primary-500 hover:text-primary-600"
                    onClick={() => downloadFile(`/studies/${studyId}/fielding/links/${r.link_id}/reconciliation.csv`,
                      'reconciliation.csv')}>
                    Reconciliation
                  </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
