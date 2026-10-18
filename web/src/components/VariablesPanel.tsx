import { useEffect, useMemo, useState } from 'react'

import { questionnaireApi, type PlannedVariable } from '../api/client'

/**
 * Every column the data file will have — hidden variables included — and what
 * fills each one.
 *
 * Hidden variables are where a data file goes wrong unseen: a coded variable
 * with no rule arrives empty, one computed from a base is only as right as that
 * base. This is the list a scripter checks against the questionnaire before
 * anyone is asked anything, and the codes to check the punched data against.
 */
const KINDS: Array<{ key: PlannedVariable['filled_by'] | 'all'; label: string; tone: string }> = [
  { key: 'all', label: 'All', tone: '' },
  { key: 'asked', label: 'Asked', tone: 'bg-slate-100 text-slate-700 ring-slate-500/20' },
  { key: 'computed', label: 'Computed', tone: 'bg-indigo-50 text-indigo-700 ring-indigo-600/20' },
  { key: 'not_filled', label: 'Not filled', tone: 'bg-amber-50 text-amber-800 ring-amber-600/20' },
  { key: 'assigned', label: 'Assigned by the link', tone: 'bg-sky-50 text-sky-700 ring-sky-600/20' },
  { key: 'recorded', label: 'Recorded', tone: 'bg-slate-50 text-slate-500 ring-slate-400/20' },
]
const TONE = Object.fromEntries(KINDS.map((k) => [k.key, k]))

export default function VariablesPanel({ studyId }: { studyId: string }) {
  const [variables, setVariables] = useState<PlannedVariable[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [kind, setKind] = useState<string>('all')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    questionnaireApi
      .datamap(studyId)
      .then((res) => setVariables(res.data.variables))
      .catch(() => setError('There is no questionnaire to list variables from yet.'))
  }, [studyId])

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: variables?.length ?? 0 }
    for (const v of variables ?? []) c[v.filled_by] = (c[v.filled_by] ?? 0) + 1
    return c
  }, [variables])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (variables ?? []).filter(
      (v) =>
        (kind === 'all' || v.filled_by === kind) &&
        (!q || v.name.toLowerCase().includes(q) || (v.label || '').toLowerCase().includes(q)),
    )
  }, [variables, kind, query])

  if (error) return <p className="text-sm text-slate-500">{error}</p>
  if (!variables) return <div className="h-40 animate-pulse rounded-lg border border-slate-200 bg-slate-50" />

  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-panel" data-testid="variables-panel">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 p-5">
        <div className="min-w-0">
          <h2 className="font-semibold text-slate-900">Variables</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Every column the data file will have, hidden ones included, and what fills it. Check the codes here
            against the punched data.
          </p>
        </div>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Find a variable"
          aria-label="Find a variable"
          className="w-56 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
        />
      </div>
      <div className="flex flex-wrap gap-2 px-5 pt-4" role="tablist" aria-label="What fills it">
        {KINDS.filter((k) => k.key === 'all' || counts[k.key]).map((k) => (
          <button
            key={k.key}
            type="button"
            role="tab"
            aria-selected={kind === k.key}
            onClick={() => setKind(k.key)}
            className={`rounded-full px-3 py-1 text-sm ring-1 ring-inset ${
              kind === k.key ? 'bg-slate-900 text-white ring-slate-900' : 'bg-white text-slate-600 ring-slate-200 hover:bg-slate-50'
            }`}
          >
            {k.label} <span className="tabular-nums opacity-70">{counts[k.key] ?? 0}</span>
          </button>
        ))}
      </div>
      {counts.not_filled ? (
        <p className="mx-5 mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {counts.not_filled} hidden variable{counts.not_filled === 1 ? '' : 's'} will arrive empty — approve a rule for
          each in Questionnaire review, or supply it with the sample.
        </p>
      ) : null}
      <div className="overflow-x-auto p-5">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="py-2 pr-4 font-medium">Variable</th>
              <th className="py-2 pr-4 font-medium">Label</th>
              <th className="py-2 pr-4 font-medium">Filled by</th>
              <th className="py-2 font-medium">Codes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {shown.map((v) => {
              const codes = Object.entries(v.values ?? {})
              return (
                <tr key={v.name} className="align-top">
                  <td className="py-2 pr-4 font-mono text-xs text-slate-800">{v.name}</td>
                  <td className="max-w-md py-2 pr-4 text-slate-700">{v.label}</td>
                  <td className="py-2 pr-4">
                    <span className={`inline-block rounded-full px-2 py-0.5 text-xs ring-1 ring-inset ${TONE[v.filled_by]?.tone ?? ''}`}>
                      {TONE[v.filled_by]?.label ?? v.filled_by}
                    </span>
                    {v.how && <p className="mt-1 max-w-xs text-xs text-slate-500">{v.how}</p>}
                  </td>
                  <td className="py-2 text-xs text-slate-600">
                    {codes.length === 0 ? (
                      <span className="text-slate-400">{v.type}</span>
                    ) : (
                      <button type="button" className="text-indigo-600 hover:underline" onClick={() => setOpen(open === v.name ? null : v.name)}>
                        {codes.length} code{codes.length === 1 ? '' : 's'}
                      </button>
                    )}
                    {open === v.name && (
                      <ul className="mt-1 space-y-0.5">
                        {codes.map(([code, label]) => (
                          <li key={code}>
                            <span className="tabular-nums font-medium text-slate-800">{code}</span> {label}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
