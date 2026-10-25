import { useCallback, useEffect, useState } from 'react'

import { fieldingApi, type FieldingLink, type FieldReport, type QuotaCell, type SupplierRow } from '../api/client'
import { useStudyEvents } from '../lib/useStudyEvents'
import InfoTip from './ui/InfoTip'

/**
 * Fieldwork control room: quota fill, completes against target, interview length,
 * incidence and the suppliers, refreshed when the server reports a respondent
 * finishing or being screened out, and every 30 s regardless.
 */
const REFRESH_MS = 30_000

interface Snapshot { cells: QuotaCell[]; suppliers: SupplierRow[]; reports: FieldReport[] }

const money = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString())

function Gauge({ label, count, limit }: { label: string; count: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, Math.round((count / limit) * 100)) : 0
  const full = count >= limit
  const color = full ? 'bg-red-400' : pct > 80 ? 'bg-amber-400' : 'bg-emerald-400'
  return (
    <div>
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span className="tabular-nums text-slate-500">
          {count} / {limit}{full ? ' · full' : pct > 80 ? ' · nearly full' : ''}
        </span>
      </div>
      <div role="meter" aria-label={`${label} quota`} aria-valuenow={count} aria-valuemin={0} aria-valuemax={limit}
        className="mt-1 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export default function ControlRoom({ studyId, links }: { studyId: string; links: FieldingLink[] }) {
  const live = links.filter((l) => !l.is_test)
  const key = live.map((l) => l.id).join(',')
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [error, setError] = useState(false)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const refresh = useCallback(async () => {
    try {
      const ids = key ? key.split(',') : []
      const [sup, quotas, reports] = await Promise.all([
        fieldingApi.suppliers(studyId),
        Promise.all(ids.map((id) => fieldingApi.listQuotas(studyId, id))),
        Promise.all(ids.map((id) => fieldingApi.fieldReport(studyId, id))),
      ])
      setSnap({
        suppliers: sup.data.vendors,
        cells: quotas.flatMap((q) => q.data.cells),
        reports: reports.map((r) => r.data),
      })
      setError(false)
      setUpdatedAt(Date.now())
    } catch {
      setError(true)
    }
  }, [studyId, key])

  useEffect(() => {
    if (!key) return
    refresh()
    const t = setInterval(refresh, REFRESH_MS)
    return () => clearInterval(t)
  }, [key, refresh])
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  useStudyEvents(key ? studyId : undefined, refresh)

  if (!key) return null

  const rows = (snap?.suppliers ?? []).filter((r) => !r.error)
  const completes = rows.reduce((a, r) => a + (r.completes ?? 0), 0)
  const target = rows.reduce((a, r) => a + (r.target_completes ?? 0), 0)
  // Weighted by starts so a quiet link does not drag the figure.
  const weighted = (pick: (r: FieldReport) => number | null) => {
    const rs = (snap?.reports ?? []).filter((r) => pick(r) != null && r.starts > 0)
    const w = rs.reduce((a, r) => a + r.starts, 0)
    return w ? rs.reduce((a, r) => a + (pick(r) as number) * r.starts, 0) / w : null
  }
  const loi = weighted((r) => r.median_loi_sec)
  const ir = weighted((r) => r.incidence_pct)
  const ago = updatedAt == null ? null : Math.max(0, Math.round((now - updatedAt) / 1000))

  return (
    <section aria-label="Control room" className="rounded-lg border border-slate-200 bg-white p-4 shadow-panel">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-semibold text-slate-900">Control room<InfoTip id="control_room" /></h2>
        <span className="text-xs text-slate-500" aria-live="off">
          {ago == null ? 'Loading…' : `Updated ${ago} s ago`}
        </span>
        <span className="flex-1" />
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-sm tabular-nums">
          {completes}{target ? ` / ${target}` : ''} completes
        </span>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-sm tabular-nums">
          LOI {loi == null ? '—' : `${Math.round(loi / 60)} min`}
        </span>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-sm tabular-nums">
          IR {ir == null ? '—' : `${Math.round(ir)}%`}
        </span>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-amber-700">
          Could not refresh just now. It will try again in a few seconds.
        </p>
      )}
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <h3 className="text-sm font-medium text-slate-700">Quotas</h3>
          {snap && snap.cells.length === 0 && <p className="text-sm text-slate-400">No quotas set.</p>}
          {(snap?.cells ?? []).map((c) => <Gauge key={c.id} label={c.name} count={c.count} limit={c.limit} />)}
        </div>
        <div>
          <h3 className="text-sm font-medium text-slate-700">Suppliers</h3>
          <table className="mt-1 w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500">
                <th scope="col" className="font-normal">Supplier</th>
                <th scope="col" className="font-normal">Done</th>
                <th scope="col" className="font-normal">Flagged</th>
                <th scope="col" className="font-normal">Cost</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const flagged = r.pct_removed
                return (
                  <tr key={r.link_id} className="border-t border-slate-100">
                    <th scope="row" className="py-1 text-left font-normal">{r.vendor_name ?? 'Unnamed'}</th>
                    <td className="tabular-nums">{r.completes}</td>
                    <td className={`tabular-nums ${flagged != null && flagged > 20 ? 'font-medium text-red-700' : ''}`}>
                      {flagged == null ? '—' : `${Math.round(flagged)}%${flagged > 20 ? ' (high)' : ''}`}
                    </td>
                    <td className="tabular-nums">{money(r.billable)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
