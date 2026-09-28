import { useEffect, useState } from 'react'

import { LiveResponses, fieldingApi } from '../api/client'

/** How often the table refreshes while it is open and the tab is visible. */
const REFRESH_MS = 5000

const STATUS_STYLE: Record<string, string> = {
  complete: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  'screened out': 'bg-amber-50 text-amber-700 ring-amber-200',
  'over quota': 'bg-rose-50 text-rose-700 ring-rose-200',
  security: 'bg-rose-50 text-rose-700 ring-rose-200',
  'in progress': 'bg-sky-50 text-sky-700 ring-sky-200',
}

/** The interview's own variables, shown before the answers they give context to. */
const SYSTEM_COLUMNS = new Set([
  'vDurationSec', 'vDevice', 'vCountry', 'vRegion', 'vCity', 'vPostcode', 'vConnection',
])

/** Readable headings — "vDurationSec" is the data file's name, not a person's. */
const SYSTEM_LABEL: Record<string, string> = {
  vDurationSec: 'Length (s)',
  vDevice: 'Device',
  vCountry: 'Country',
  vRegion: 'Region',
  vCity: 'City',
  vPostcode: 'Postcode',
  vConnection: 'Connection',
}

function ago(iso: string | null): string {
  if (!iso) return '—'
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return `${Math.round(seconds)}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  return new Date(iso).toLocaleString()
}

/**
 * Respondent answers as they arrive.
 *
 * What a researcher watches in the first hour of field: is anyone getting
 * through, where do they drop, are the answers sane. Refreshes on its own and
 * stops while the tab is hidden, so an open panel costs nothing overnight.
 * Access follows the study's data rule — the refusal says when it opens.
 */
export default function LiveResponsesPanel({ studyId, linkId }: { studyId: string; linkId: string }) {
  const [data, setData] = useState<LiveResponses | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updated, setUpdated] = useState<Date | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      if (document.visibilityState === 'hidden') return
      try {
        const res = await fieldingApi.liveResponses(studyId, linkId)
        if (cancelled) return
        setData(res.data)
        setRefused(null)
        setError(null)
        setUpdated(new Date())
      } catch (err: unknown) {
        if (cancelled) return
        const response = (err as { response?: { status?: number; data?: { detail?: string } } }).response
        if (response?.status === 403) setRefused(response.data?.detail ?? 'Not available yet.')
        else setError('Could not load responses.')
      }
    }
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [studyId, linkId])

  if (refused) {
    return (
      <p className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
        {refused}
      </p>
    )
  }
  if (error) return <p className="mt-3 text-sm text-red-600">{error}</p>
  if (!data) return <p className="mt-3 text-sm text-slate-400">Loading responses…</p>

  return (
    <div className="mt-3 border-t border-slate-100 pt-4">
      <div className="mb-3 flex items-center gap-3 text-sm">
        <span className="inline-flex items-center gap-1.5 font-medium text-slate-800">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60 motion-reduce:animate-none" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
          </span>
          Live
        </span>
        <span className="tabular-nums text-slate-500">
          {data.total} {data.total === 1 ? 'respondent' : 'respondents'}
          {data.rows.length < data.total && ` · newest ${data.rows.length} shown`}
        </span>
        {updated && <span className="text-xs text-slate-400">updated {updated.toLocaleTimeString()}</span>}
        {data.location_available === false && (
          /* A blank meaning "not configured" looks exactly like a blank meaning
             "we could not tell", so it is said rather than left to be guessed. */
          <span className="text-xs text-amber-600">
            Country, city and postcode need the offline location database on the
            server (DVV_GEOIP_DB) — they stay blank without it.
          </span>
        )}
      </div>

      {data.rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500">
          Nobody has opened the link yet. The first respondent appears here within seconds.
        </p>
      ) : (
        <div className="max-h-[480px] overflow-auto rounded-xl border border-slate-200">
          <table className="min-w-full text-sm">
            <thead className="sticky top-0 z-10 bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="sticky left-0 bg-slate-50 px-3 py-2 font-medium">Respondent</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Last seen</th>
                {data.columns.map((qid) => (
                  <th
                    key={qid}
                    className={
                      SYSTEM_COLUMNS.has(qid)
                        ? 'whitespace-nowrap bg-slate-100/70 px-3 py-2 font-medium text-slate-600'
                        : 'whitespace-nowrap px-3 py-2 font-mono font-medium'
                    }
                  >
                    {SYSTEM_LABEL[qid] ?? qid}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.rows.map((row) => (
                <tr key={row.id} className="hover:bg-slate-50/70">
                  <td className="sticky left-0 bg-white px-3 py-2 font-mono text-xs text-slate-500">{row.id}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                        STATUS_STYLE[row.status] ?? STATUS_STYLE['in progress']
                      }`}
                    >
                      {row.status}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-500">{ago(row.last_seen_at)}</td>
                  {data.columns.map((qid) => (
                    <td
                      key={qid}
                      className={`max-w-[220px] truncate px-3 py-2 ${
                        SYSTEM_COLUMNS.has(qid)
                          ? 'bg-slate-50/60 font-mono text-xs text-slate-500'
                          : 'text-slate-700'
                      }`}
                      title={row.answers[qid]}
                    >
                      {row.answers[qid] ?? <span className="text-slate-300">—</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
