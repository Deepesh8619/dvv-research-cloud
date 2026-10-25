import { useEffect, useState } from 'react'

import { studiesApi, type CrosstabsPayload } from '../api/client'
import CrosstabTable from './CrosstabTable'
import StudyThread from './StudyThread'

/** The study's computed tables; click any number to open its thread. */
export default function TablesPanel({ studyId }: { studyId: string }) {
  const [tabs, setTabs] = useState<CrosstabsPayload | null>(null)
  const [tableId, setTableId] = useState('')
  const [cell, setCell] = useState<{ row: number; col: number } | null>(null)

  useEffect(() => {
    studiesApi.crosstabs(studyId).then((r) => {
      setTabs(r.data)
      setTableId(r.data.tables[0]?.id ?? '')
    }).catch(() => setTabs(null))
  }, [studyId])

  const table = tabs?.tables.find((t) => t.id === tableId)
  if (!tabs || !table) return null

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-panel" data-testid="tables-panel">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-semibold text-slate-900">Tables</h2>
        <label className="text-sm text-slate-600">
          <span className="sr-only">Table</span>
          <select
            value={tableId}
            onChange={(e) => { setTableId(e.target.value); setCell(null) }}
            className="rounded border border-slate-300 px-2 py-1 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          >
            {tabs.tables.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select>
        </label>
      </div>
      <p className="mt-1 text-sm text-slate-500">Click a number to see where it came from.</p>
      <div className="mt-3">
        <CrosstabTable
          table={table}
          minBase={tabs.min_base_for_test}
          showBases
          selected={cell}
          onCellClick={(row, col) => setCell({ row, col })}
        />
      </div>
      {cell && (
        <StudyThread studyId={studyId} tableId={table.id} row={cell.row} col={cell.col} onClose={() => setCell(null)} />
      )}
    </section>
  )
}
