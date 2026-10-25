import { useCallback, useEffect, useRef, useState } from 'react'

import { reportApi, studiesApi, type ReportCite, type ReportPayload, type ReportSentence } from '../api/client'
import type { TabTable } from './CrosstabTable'
import InfoTip from './ui/InfoTip'

/**
 * The cited report: each sentence names the table cells it rests on, and code
 * (not a model) decides whether it stands. A refused sentence stays, struck
 * through, with what to change underneath.
 */
function cellText(tables: TabTable[], c: ReportCite) {
  const t = tables.find((x) => x.id === c.table)
  const col = t?.columns[c.col]
  const row = t?.rows[c.row]
  if (!t || !col || !row) return 'This cell no longer exists'
  return `${row.label}, ${col.label}: ${row.pct[c.col].toFixed(0)}% · base ${col.unweighted_base}`
}

const chipLabel = (c: ReportCite) => `${c.table.toUpperCase()}·r${c.row + 1}c${c.col + 1}`

function Chips({ cites, tables }: { cites: ReportCite[]; tables: TabTable[] }) {
  return (
    <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
      {cites.map((c, i) => (
        <button
          key={i}
          type="button"
          title={cellText(tables, c)}
          aria-label={`Citation ${i + 1}: ${cellText(tables, c)}`}
          className="group relative rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600"
        >
          {chipLabel(c)}
          <span role="tooltip" className="pointer-events-none absolute left-0 top-full z-10 mt-1 hidden w-max max-w-xs rounded bg-slate-900 px-2 py-1 font-sans text-xs text-white group-hover:block group-focus:block">
            {cellText(tables, c)}
          </span>
        </button>
      ))}
    </span>
  )
}

function CitePicker({ tables, value, onChange }: { tables: TabTable[]; value: ReportCite[]; onChange: (c: ReportCite[]) => void }) {
  const set = (i: number, patch: Partial<ReportCite>) =>
    onChange(value.map((c, k) => (k === i ? { ...c, ...patch } : c)))
  return (
    <div className="space-y-1">
      {value.map((c, i) => {
        const t = tables.find((x) => x.id === c.table)
        return (
          <div key={i} className="flex flex-wrap items-center gap-1 text-xs">
            <select aria-label={`Table for citation ${i + 1}`} value={c.table} className="rounded border border-slate-300 px-1 py-1"
              onChange={(e) => set(i, { table: e.target.value, row: 0, col: 0 })}>
              {tables.map((x) => <option key={x.id} value={x.id}>{x.id.toUpperCase()} {x.title.slice(0, 40)}</option>)}
            </select>
            <select aria-label={`Row for citation ${i + 1}`} value={c.row} className="rounded border border-slate-300 px-1 py-1"
              onChange={(e) => set(i, { row: Number(e.target.value) })}>
              {(t?.rows ?? []).map((r, k) => <option key={k} value={k}>{r.label}</option>)}
            </select>
            <select aria-label={`Column for citation ${i + 1}`} value={c.col} className="rounded border border-slate-300 px-1 py-1"
              onChange={(e) => set(i, { col: Number(e.target.value) })}>
              {(t?.columns ?? []).map((x, k) => <option key={k} value={k}>{x.label}</option>)}
            </select>
            <button type="button" className="rounded px-1.5 py-1 text-slate-600 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600"
              onClick={() => onChange(value.filter((_, k) => k !== i))}>Remove cell {i + 1}</button>
          </div>
        )
      })}
      <button type="button" disabled={tables.length === 0}
        className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600"
        onClick={() => onChange([...value, { table: tables[0].id, row: 0, col: 0 }])}>Add a cited cell</button>
    </div>
  )
}

function Editor({ initial, cites, tables, onSave, onCancel }: {
  initial: string; cites: ReportCite[]; tables: TabTable[]
  onSave: (text: string, cites: ReportCite[]) => void; onCancel: () => void
}) {
  const [text, setText] = useState(initial)
  const [picked, setPicked] = useState(cites)
  return (
    <div className="space-y-2 rounded border border-slate-200 bg-slate-50 p-3">
      <label className="block text-xs font-medium text-slate-600" htmlFor="report-sentence">Sentence</label>
      <textarea id="report-sentence" value={text} onChange={(e) => setText(e.target.value)} rows={2}
        className="w-full rounded border border-slate-300 p-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600" />
      <CitePicker tables={tables} value={picked} onChange={setPicked} />
      <div className="flex gap-2">
        <button type="button" disabled={!text.trim()} onClick={() => onSave(text, picked)}
          className="rounded bg-primary-600 px-3 py-1 text-sm text-white disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600">Save sentence</button>
        <button type="button" onClick={onCancel}
          className="rounded px-3 py-1 text-sm text-slate-600 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">Cancel</button>
      </div>
    </div>
  )
}

export default function ReportPanel({ studyId }: { studyId: string }) {
  const [report, setReport] = useState<ReportPayload | null>(null)
  const [tables, setTables] = useState<TabTable[]>([])
  const [editing, setEditing] = useState<string | null>(null) // sentence id, or 'new'
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  const load = useCallback(() => {
    reportApi.get(studyId).then((r) => {
      setReport(r.data)
      if (r.data.job?.state === 'running') timer.current = setTimeout(load, 1500)
    }).catch(() => setReport(null))
  }, [studyId])

  useEffect(() => {
    load()
    studiesApi.crosstabs(studyId).then((r) => setTables(r.data.tables)).catch(() => setTables([]))
    return () => clearTimeout(timer.current)
  }, [studyId, load])

  if (!report || tables.length === 0) return null
  const running = report.job?.state === 'running'
  const fail = (e: unknown) => {
    const d = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
    setError(d ?? 'That did not work. Try again in a moment.')
  }
  const draft = () => { setError(null); reportApi.draft(studyId).then(load).catch(fail) }
  const save = (text: string, cites: ReportCite[], sid?: string) => {
    setError(null)
    const call = sid ? reportApi.edit(studyId, sid, text, cites) : reportApi.add(studyId, text, cites)
    call.then(() => { setEditing(null); load() }).catch(fail)
  }
  const accepted = report.sentences.filter((s) => s.ok).length
  const refused = report.sentences.length - accepted
  const byId = (s: ReportSentence) => s.id

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-panel" data-testid="report">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-semibold text-slate-900">Cited report<InfoTip id="report" /></h2>
        {report.sentences.length > 0 && (
          <span className="text-xs text-slate-500">{accepted} cited{refused > 0 ? `, ${refused} refused` : ''}</span>
        )}
        <span className="flex-1" />
        <button type="button" onClick={() => setEditing('new')}
          className="rounded border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">Add sentence</button>
        <button type="button" onClick={draft} disabled={running}
          className="rounded bg-primary-600 px-3 py-1 text-sm text-white disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600">
          {running ? 'Drafting…' : report.sentences.length ? 'Draft again' : 'Draft report'}
        </button>
      </div>
      <p className="mt-1 max-w-2xl text-sm text-slate-500">
        Drafted from the findings and checked by code. Drafting again replaces the sentences below.
      </p>
      {(error || report.job?.state === 'failed') && (
        <p role="alert" className="mt-2 text-sm text-red-700">{error ?? report.job?.error}</p>
      )}
      <ol className="mt-4 space-y-3">
        {report.sentences.map((s) => (
          <li key={byId(s)} className="text-sm">
            {editing === s.id ? (
              <Editor initial={s.text} cites={s.cites} tables={tables} onCancel={() => setEditing(null)}
                onSave={(t, c) => save(t, c, s.id)} />
            ) : (
              <>
                <button type="button" onClick={() => setEditing(s.id)} aria-label={`Edit: ${s.text}`}
                  className={`text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600 ${s.ok ? 'text-slate-800' : 'text-slate-400 line-through'}`}>
                  {s.text}
                </button>
                <Chips cites={s.cites} tables={tables} />
                {!s.ok && <span className="ml-2 rounded bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-700">refused</span>}
                <button type="button" onClick={() => reportApi.remove(studyId, s.id).then(load).catch(fail)}
                  className="ml-2 text-xs text-slate-500 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">Remove</button>
                {!s.ok && (
                  <ul className="mt-1 list-disc pl-5 text-xs text-red-700">
                    {s.problems.map((p, i) => <li key={i}>{p}</li>)}
                  </ul>
                )}
              </>
            )}
          </li>
        ))}
      </ol>
      {editing === 'new' && (
        <div className="mt-3">
          <Editor initial="" cites={[]} tables={tables} onCancel={() => setEditing(null)} onSave={(t, c) => save(t, c)} />
        </div>
      )}
    </section>
  )
}
