import { useEffect, useRef, useState } from 'react'

import { studiesApi, type CellProvenance } from '../api/client'
import InfoTip from './ui/InfoTip'

/**
 * A drawer from the right: the trail behind one table cell, drawn as a vertical
 * thread of knots. Escape or the close button dismiss it, and focus goes back to
 * the cell that opened it.
 */
interface Props {
  studyId: string
  tableId: string
  row: number
  col: number
  onClose: () => void
}

export default function StudyThread({ studyId, tableId, row, col, onClose }: Props) {
  const [data, setData] = useState<CellProvenance | null>(null)
  const [failed, setFailed] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    return () => opener?.focus?.()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    setData(null)
    setFailed(false)
    studiesApi.cellProvenance(studyId, tableId, row, col).then((r) => setData(r.data)).catch(() => setFailed(true))
  }, [studyId, tableId, row, col])

  return (
    <aside
      role="dialog"
      aria-label="Study thread"
      className="fixed inset-y-0 right-0 z-40 w-full max-w-md overflow-y-auto border-l border-slate-200 bg-white p-6 shadow-panel"
    >
      <div className="flex items-start justify-between gap-3">
        <h2 className="font-semibold text-slate-900">
          {data ? `Why ${data.value.toFixed(1)}%` : 'Study thread'}
          <InfoTip id="thread" />
        </h2>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          className="rounded px-2 py-1 text-sm text-slate-600 hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
        >
          Close
        </button>
      </div>
      {failed && <p className="mt-4 text-sm text-red-700">The trail for this number could not be loaded. Close this and try again.</p>}
      {!data && !failed && <p className="mt-4 text-sm text-slate-500">Loading…</p>}
      {data && (
        <ol className="mt-5 border-l-2 border-primary-200 pl-5">
          {data.steps.map((s, i) => (
            <li key={i} className="relative pb-5 last:pb-0" data-testid="knot">
              <span aria-hidden className="absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 border-primary-500 bg-white" />
              <b className="block text-sm text-slate-900">{s.title}</b>
              <span className="text-sm text-slate-600">{s.detail}</span>
            </li>
          ))}
        </ol>
      )}
    </aside>
  )
}
