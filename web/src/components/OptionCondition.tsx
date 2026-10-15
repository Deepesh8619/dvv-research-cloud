import { useState } from 'react'

import type { Expression } from '../api/client'
import RoutingEditor, { fromFlatRouting, toFlatRouting } from './RoutingEditor'

/**
 * "Show this answer only if…" — a condition on ONE answer, not the question.
 * "Yes, for a child under 18 [ONLY SHOW IF S11=1]", or a brand offered only to
 * its current users. The parser reads these from the document; this is where a
 * person sees, changes or adds one. Collapsed to a badge until opened, so a
 * list of thirty answers stays readable.
 */
export function describe(expr: Expression | null | undefined): string {
  if (!expr) return ''
  if (expr.verbatim_text) return expr.verbatim_text
  if (expr.op === 'and' || expr.op === 'or') {
    return (expr.children ?? []).map(describe).join(` ${expr.op.toUpperCase()} `)
  }
  const value = expr.values?.length ? expr.values.join(', ') : String(expr.value ?? '')
  return `${expr.qid} ${expr.op} ${value}`
}

export default function OptionCondition({
  condition,
  onChange,
  qidOptions,
  disabled,
}: {
  condition: Expression | null | undefined
  onChange: (next: Expression | null) => void
  qidOptions: string[]
  disabled: boolean
}) {
  const [open, setOpen] = useState(false)
  // The editor's working copy: a blank line being filled in is not yet a
  // condition, and rebuilding from the saved one would drop it mid-typing.
  const [flat, setFlat] = useState(() => toFlatRouting(condition ?? null))
  const summary = describe(condition)
  return (
    <div className="ml-24">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={disabled && !summary}
        className={`text-[11px] ${summary ? 'text-amber-700' : 'text-slate-400'} hover:underline disabled:hidden`}
        aria-label="answer condition"
      >
        {summary ? `Only shown if: ${summary}` : '+ only show this answer if…'}
      </button>
      {open && (
        <div className="mt-1 border border-slate-200 rounded-md p-2 bg-slate-50">
          <RoutingEditor
            label="Show this answer only if"
            emptyText="Everyone who sees the question sees this answer"
            flat={flat}
            onChange={(next) => {
              setFlat(next)
              onChange(fromFlatRouting(next, condition?.verbatim_text ?? null))
            }}
            qidOptions={qidOptions}
            disabled={disabled}
          />
        </div>
      )}
    </div>
  )
}
