import { useMemo, useState } from 'react'
import type { VariableMatch, VariableMatchRow, VariableRule } from '../api/client'

type Filter = 'all' | 'broken' | 'missing_in_data' | 'not_in_questionnaire' | 'not_checked' | 'met'

const STATUS: Record<VariableMatchRow['status'], { label: string; cls: string }> = {
  broken: { label: 'Rule broken', cls: 'bg-red-50 text-red-700' },
  warning: { label: 'Check', cls: 'bg-amber-50 text-amber-800' },
  missing_in_data: { label: 'Not in the data', cls: 'bg-red-50 text-red-700' },
  not_in_questionnaire: { label: 'Not in the questionnaire', cls: 'bg-amber-50 text-amber-800' },
  not_checked: { label: 'Not checked', cls: 'bg-slate-100 text-slate-600' },
  met: { label: 'All rules met', cls: 'bg-emerald-50 text-emerald-700' },
}

const RULE_CLS: Record<VariableRule['result'], string> = {
  met: 'border-emerald-200 text-emerald-700',
  broken: 'border-red-300 bg-red-50 text-red-700',
  warning: 'border-amber-300 bg-amber-50 text-amber-800',
}

function RuleChip({ rule }: { rule: VariableRule }) {
  const mark = rule.result === 'met' ? '✓' : rule.result === 'broken' ? '✗' : '!'
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] ${RULE_CLS[rule.result]}`}
      title={rule.detail}>
      <span aria-hidden>{mark}</span>
      {rule.label}
      {rule.result !== 'met' && rule.n_rows ? <span className="tabular-nums">· {rule.n_rows}</span> : null}
      <span className="sr-only">{rule.result}</span>
    </span>
  )
}

function Row({ row }: { row: VariableMatchRow }) {
  const [open, setOpen] = useState(false)
  const problems = row.rules.filter((r) => r.result !== 'met')
  return (
    <li className="border-b border-slate-100 py-2 last:border-0">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 text-left">
        <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${STATUS[row.status].cls}`}>
          {STATUS[row.status].label}
        </span>
        <span className="font-mono text-xs text-slate-900">{row.qid}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-slate-600">{row.text}</span>
        <span className="font-mono text-[11px] text-slate-400">
          {row.n_columns ? `${row.columns.slice(0, 3).join(', ')}${row.n_columns > 3 ? ` +${row.n_columns - 3}` : ''}` : 'no column'}
        </span>
      </button>
      {row.rules.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {row.rules.map((r) => <RuleChip key={r.rule} rule={r} />)}
        </div>
      )}
      {open && (
        <div className="mt-2 space-y-1 rounded bg-slate-50 p-2 text-xs text-slate-700">
          {row.status === 'missing_in_data' && <p>The questionnaire asks this, but no column in the data holds it.</p>}
          {row.status === 'not_in_questionnaire' && <p>The data has this column, and the questionnaire never asks it.</p>}
          {row.status === 'not_checked' && <p>Found in the data, but no rule from the questionnaire could be tested on it.</p>}
          {problems.map((r) => (
            <p key={r.rule}>
              <span className="font-medium">{r.label}:</span> {r.detail}
              {r.example_rows && r.example_rows.length > 0 && (
                <span className="text-slate-500"> (rows {r.example_rows.join(', ')})</span>
              )}
            </p>
          ))}
          {row.n_columns > 0 && <p className="font-mono text-[11px] text-slate-500">Columns: {row.columns.join(', ')}</p>}
        </div>
      )}
    </li>
  )
}

/**
 * Questionnaire variables against data columns, one row each: every rule tested
 * on it — codes, skips, screen-outs, masks, piping, exclusive answers, ranges —
 * met or broken, then the data columns no question claims.
 */
export default function VariableMatchTable({ match }: { match: VariableMatch }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const count = (s: string) => match.questions.filter((q) => q.status === s || (s === 'broken' && q.status === 'warning')).length
  const filters: Array<{ key: Filter; label: string; n: number }> = [
    { key: 'all', label: 'All', n: match.questions.length },
    { key: 'broken', label: 'Rule broken', n: count('broken') },
    { key: 'missing_in_data', label: 'Not in the data', n: count('missing_in_data') },
    { key: 'not_in_questionnaire', label: 'Not in the questionnaire', n: count('not_in_questionnaire') },
    { key: 'not_checked', label: 'Not checked', n: count('not_checked') },
    { key: 'met', label: 'All rules met', n: count('met') },
  ]
  const shown = useMemo(() => {
    const term = search.trim().toLowerCase()
    return match.questions.filter((q) =>
      (filter === 'all' || q.status === filter || (filter === 'broken' && q.status === 'warning'))
      && (!term || q.qid.toLowerCase().includes(term) || q.text.toLowerCase().includes(term)
        || q.columns.some((c) => c.toLowerCase().includes(term))))
  }, [match.questions, filter, search])
  const toChase = match.data_only.filter((d) => d.to_chase)
  const byRole = match.data_only.filter((d) => !d.to_chase).reduce<Record<string, string[]>>((acc, d) => {
    (acc[d.role_label] ||= []).push(d.column)
    return acc
  }, {})

  return (
    <div className="space-y-3" data-testid="variable-match">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-900">Variable by variable</h4>
        <p className="text-xs text-slate-500">
          {match.summary.rules_met ?? 0} rules met · {match.summary.rules_broken ?? 0} broken ·{' '}
          {match.summary.data_only ?? 0} data columns no question claims
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {filters.map((f) => (
          <button key={f.key} type="button" onClick={() => setFilter(f.key)} aria-pressed={filter === f.key}
            className={`rounded-full border px-2.5 py-0.5 text-xs ${filter === f.key
              ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300 text-slate-600 hover:border-slate-500'}`}>
            {f.label} <span className="tabular-nums opacity-70">{f.n}</span>
          </button>
        ))}
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find a question or column"
          aria-label="Find a question or column"
          className="ml-auto min-w-0 rounded border border-slate-300 px-2 py-0.5 text-xs" />
      </div>
      <ul className="max-h-[32rem] overflow-y-auto rounded-md border border-slate-200 bg-white px-3">
        {shown.length === 0 && <li className="py-3 text-xs text-slate-500">No questions match.</li>}
        {shown.map((row) => <Row key={row.qid} row={row} />)}
      </ul>
      {match.data_only.length > 0 && (
        <details className="text-xs text-slate-600">
          <summary className="cursor-pointer text-slate-500 hover:text-slate-700">
            {match.data_only.length} data columns no question claims
            {toChase.length > 0 ? ` · ${toChase.length} look like questions` : ''}
          </summary>
          {toChase.length > 0 && (
            <p className="mt-2">
              <span className="font-medium text-amber-800">Look like questions, worth chasing: </span>
              <span className="font-mono">{toChase.map((d) => d.column).join(', ')}</span>
            </p>
          )}
          {Object.entries(byRole).map(([role, cols]) => (
            <p key={role} className="mt-1">
              <span className="font-medium">{role} ({cols.length}): </span>
              <span className="font-mono text-slate-500">{cols.slice(0, 20).join(', ')}{cols.length > 20 ? ` +${cols.length - 20}` : ''}</span>
            </p>
          ))}
        </details>
      )}
    </div>
  )
}
