import { useCallback, useEffect, useState } from 'react'
import { LifecycleResponse, studiesApi } from '../api/client'
import { DATA_CHANGED } from './ProjectDataBar'
import InfoTip from './ui/InfoTip'

const BORDER = { done: 'border-t-emerald-500', attention: 'border-t-amber-500', todo: 'border-t-slate-200' }
const MARK = { done: '✓', attention: 'needs you', todo: '' }

export default function LifecycleRail({ studyId, onOpen }: { studyId: string; onOpen: (tab: string) => void }) {
  const [data, setData] = useState<LifecycleResponse | null>(null)
  const load = useCallback(() => {
    studiesApi.lifecycle(studyId).then((r) => setData(r.data)).catch(() => {})
  }, [studyId])
  useEffect(() => {
    load()
    window.addEventListener(DATA_CHANGED, load)
    return () => window.removeEventListener(DATA_CHANGED, load)
  }, [load])
  if (!data) return null

  return (
    <section aria-label="Project lifecycle" className="mb-4">
      <div className="mb-1 flex items-center text-xs font-medium text-slate-500">
        Lifecycle<InfoTip id="lifecycle" />
      </div>
      <ol className="grid grid-cols-2 gap-0.5 overflow-hidden rounded-lg border border-slate-200 bg-slate-200 shadow-panel sm:grid-cols-4 xl:grid-cols-8">
        {data.stages.map((s) => (
          <li key={s.key}>
            <button
              type="button"
              onClick={() => onOpen(s.tab)}
              title={s.detail}
              aria-label={`${s.label}: ${s.state === 'done' ? 'done' : s.state === 'attention' ? 'needs you' : 'to do'}. ${s.detail}`}
              className={`h-full w-full border-t-4 bg-white px-3 py-2 text-left text-sm hover:bg-slate-50 focus-visible:relative focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500/60 ${BORDER[s.state]}`}
            >
              <span className="flex items-baseline justify-between gap-1">
                <span className="truncate font-medium text-slate-800">{s.label}</span>
                <span className={`shrink-0 text-xs ${s.state === 'attention' ? 'font-medium text-amber-700' : 'text-emerald-700'}`}>
                  {MARK[s.state]}
                </span>
              </span>
              <span className="mt-0.5 block truncate text-xs text-slate-500">{s.detail}</span>
            </button>
          </li>
        ))}
      </ol>
      {data.needs_you.length > 0 && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3">
          <h3 className="text-sm font-semibold text-slate-800">Needs you</h3>
          <ul className="mt-1 space-y-1">
            {data.needs_you.map((n) => (
              <li key={n.kind} className="flex items-center gap-2 text-sm">
                <span aria-hidden>⚑</span>
                <span className="font-medium text-slate-800">{n.title}</span>
                <span className="text-slate-500">· {n.detail}</span>
                <button
                  type="button"
                  onClick={() => onOpen(n.tab)}
                  className="ml-auto rounded px-2 py-0.5 text-primary-600 underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40"
                >
                  Open
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
