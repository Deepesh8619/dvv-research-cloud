import { useEffect, useState } from 'react'

import { studiesApi, type Finding } from '../api/client'
import InfoTip from './ui/InfoTip'

/**
 * What the tables say, computed from them: significant differences first, then
 * the leading answers; who was screened in comes last, as the sample. Every
 * number names the table, column and base it was read from.
 */
function Cites({ f }: { f: Finding }) {
  return (
    <span className="mt-1 flex flex-wrap gap-1">
      {f.cites.map((c, i) => (
        <span key={i} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600">
          {c.table.toUpperCase()} · {c.column} {c.value.toFixed(0)}% · n={c.base}
        </span>
      ))}
    </span>
  )
}

export default function FindingsPanel({ studyId }: { studyId: string }) {
  const [found, setFound] = useState<Finding[] | null>(null)

  useEffect(() => {
    studiesApi.findings(studyId).then((r) => setFound(r.data.findings)).catch(() => setFound(null))
  }, [studyId])

  if (!found || found.length === 0) return null
  const key = found.filter((f) => f.kind !== 'sample')
  const sample = found.filter((f) => f.kind === 'sample')

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-panel" data-testid="findings">
      <h2 className="font-semibold text-slate-900">Key findings<InfoTip id="findings" /></h2>
      <p className="mt-1 max-w-2xl text-sm text-slate-500">
        Read off the tables, not written by a model; the same findings open the PowerPoint.
      </p>
      <ol className="mt-4 space-y-3">
        {key.map((f, i) => (
          <li key={i} className="text-sm text-slate-800">
            {f.kind === 'difference' && (
              <span className="mr-2 rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700">
                significant
              </span>
            )}
            {f.text}
            {f.caution && <span className="ml-1 text-amber-700">{f.caution}</span>}
            <Cites f={f} />
          </li>
        ))}
      </ol>
      {sample.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-sm text-slate-600">Who was interviewed (screening questions)</summary>
          <ul className="mt-2 space-y-2">
            {sample.map((f, i) => <li key={i} className="text-sm text-slate-600">{f.text}<Cites f={f} /></li>)}
          </ul>
        </details>
      )}
    </section>
  )
}
