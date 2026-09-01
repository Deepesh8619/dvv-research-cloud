import { Expression, ExprOp } from '../api/client'

// --- Routing editor -----------------------------------------------------------
//
// SCOPE DECISION (v1): routing is a structured Expression AST (see
// src/core/survey_definition.py). A full recursive visual tree editor is out of
// scope for v1. Instead this editor models a FLAT LIST of leaf conditions joined
// by a single AND/OR connective — i.e. it can represent:
//
//   {op: 'and'|'or', children: [<leaf>, <leaf>, ...]}   (2+ conditions)
//   <leaf>                                               (exactly 1 condition)
//   null                                                 (no conditions)
//
// where each <leaf> is {op: eq|ne|gt|lt|in|selected, qid, value|values}. This
// covers the overwhelming majority of real questionnaire routing ("ASK IF Q3 =
// YES", "TERMINATE IF AGE < 18", "SHOW IF Q5 IN (1,2)"). Nested/mixed AND-OR
// groups and `not` are NOT editable here; if extraction produced such a tree we
// fall back to showing it read-only as JSON so a reviewer can still SEE it (and
// the verbatim_text beside it) even though they can't restructure it in the form.
// The verbatim_text is always preserved on save and shown side-by-side upstream.

const LEAF_OPS: Array<{ value: ExprOp; label: string }> = [
  { value: 'eq', label: 'equals' },
  { value: 'ne', label: 'not equals' },
  { value: 'gt', label: 'greater than' },
  { value: 'lt', label: 'less than' },
  { value: 'in', label: 'is one of' },
  { value: 'selected', label: 'has selected' },
]

const LEAF_OP_SET = new Set(LEAF_OPS.map((o) => o.value))

export interface FlatRouting {
  connective: 'and' | 'or'
  conditions: Expression[]
  // Non-editable structure (nested/not) we could not flatten — shown read-only.
  raw: Expression | null
  editable: boolean
}

/** Decompose a stored Expression into the flat form this editor understands.
 *  Returns editable=false when the tree is too complex to represent flatly. */
export function toFlatRouting(expr: Expression | null): FlatRouting {
  if (!expr) return { connective: 'and', conditions: [], raw: null, editable: true }
  if (LEAF_OP_SET.has(expr.op)) {
    return { connective: 'and', conditions: [stripVerbatim(expr)], raw: expr, editable: true }
  }
  if ((expr.op === 'and' || expr.op === 'or') && expr.children) {
    const allLeaves = expr.children.every((c) => LEAF_OP_SET.has(c.op))
    if (allLeaves) {
      return {
        connective: expr.op,
        conditions: expr.children.map(stripVerbatim),
        raw: expr,
        editable: true,
      }
    }
  }
  // 'not', nested groups, or mixed — not flatly editable.
  return { connective: 'and', conditions: [], raw: expr, editable: false }
}

function stripVerbatim(e: Expression): Expression {
  const { verbatim_text: _omit, ...rest } = e
  return rest
}

/** Rebuild an Expression from the flat form, re-attaching verbatim_text at the
 *  top. Returns null when there are no conditions. */
export function fromFlatRouting(
  flat: FlatRouting,
  verbatimText: string | null,
): Expression | null {
  const conds = flat.conditions.filter((c) => c.qid && c.qid.trim())
  if (conds.length === 0) return null
  const vt = verbatimText && verbatimText.trim() ? verbatimText : undefined
  if (conds.length === 1) {
    return { ...conds[0], verbatim_text: vt ?? conds[0].verbatim_text }
  }
  return { op: flat.connective, children: conds, verbatim_text: vt }
}

function emptyLeaf(): Expression {
  return { op: 'eq', qid: '', value: '' }
}

interface Props {
  label: string
  flat: FlatRouting
  onChange: (flat: FlatRouting) => void
  qidOptions: string[] // prior qids available to reference
  disabled?: boolean
  /** Shown when there are no conditions. "Always shown" is wrong under a
      terminate rule, so each caller says what empty means for it. */
  emptyText?: string
}

export default function RoutingEditor({ label, flat, onChange, qidOptions, disabled, emptyText }: Props) {
  if (!flat.editable) {
    return (
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</span>
          <span className="text-[11px] text-amber-600">Complex routing — read only</span>
        </div>
        <pre className="text-xs bg-slate-50 border border-slate-200 rounded-md p-2 overflow-x-auto text-slate-700">
          {JSON.stringify(flat.raw, null, 2)}
        </pre>
      </div>
    )
  }

  const setCondition = (i: number, patch: Partial<Expression>) => {
    const conditions = flat.conditions.map((c, idx) => (idx === i ? { ...c, ...patch } : c))
    onChange({ ...flat, conditions })
  }

  const removeCondition = (i: number) => {
    onChange({ ...flat, conditions: flat.conditions.filter((_, idx) => idx !== i) })
  }

  const addCondition = () => {
    onChange({ ...flat, conditions: [...flat.conditions, emptyLeaf()] })
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</span>
        {flat.conditions.length > 1 && (
          <select
            value={flat.connective}
            disabled={disabled}
            onChange={(e) => onChange({ ...flat, connective: e.target.value as 'and' | 'or' })}
            className="h-7 px-2 rounded-md border border-slate-300 text-xs disabled:bg-slate-50"
            aria-label={`${label} connective`}
          >
            <option value="and">match ALL (and)</option>
            <option value="or">match ANY (or)</option>
          </select>
        )}
      </div>

      {flat.conditions.length === 0 ? (
        <p className="text-sm text-slate-400 mb-2">{emptyText ?? 'No condition — always shown.'}</p>
      ) : (
        <div className="space-y-2">
          {flat.conditions.map((cond, i) => {
            const usesValues = cond.op === 'in'
            return (
              <div key={i} className="flex items-center gap-2 flex-wrap">
                <select
                  value={cond.qid ?? ''}
                  disabled={disabled}
                  onChange={(e) => setCondition(i, { qid: e.target.value })}
                  className="h-8 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
                  aria-label="condition question"
                >
                  <option value="">select question…</option>
                  {qidOptions.map((q) => (
                    <option key={q} value={q}>
                      {q}
                    </option>
                  ))}
                  {cond.qid && !qidOptions.includes(cond.qid) && (
                    <option value={cond.qid}>{cond.qid}</option>
                  )}
                </select>
                <select
                  value={cond.op}
                  disabled={disabled}
                  onChange={(e) => {
                    const op = e.target.value as ExprOp
                    // Migrate value<->values when switching to/from `in`.
                    if (op === 'in') setCondition(i, { op, values: cond.values ?? [], value: undefined })
                    else setCondition(i, { op, value: cond.value ?? '', values: undefined })
                  }}
                  className="h-8 px-2 rounded-md border border-slate-300 text-sm disabled:bg-slate-50"
                  aria-label="condition operator"
                >
                  {LEAF_OPS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
                {usesValues ? (
                  <input
                    value={(cond.values ?? []).join(', ')}
                    disabled={disabled}
                    onChange={(e) =>
                      setCondition(i, {
                        values: e.target.value
                          .split(',')
                          .map((s) => coerce(s.trim()))
                          .filter((v) => v !== ''),
                      })
                    }
                    placeholder="codes e.g. 1, 2, 3"
                    className="h-8 px-2 rounded-md border border-slate-300 text-sm flex-1 min-w-[120px] disabled:bg-slate-50"
                    aria-label="condition values"
                  />
                ) : (
                  <input
                    value={cond.value == null ? '' : String(cond.value)}
                    disabled={disabled}
                    onChange={(e) => setCondition(i, { value: coerce(e.target.value) })}
                    placeholder="value / code"
                    className="h-8 px-2 rounded-md border border-slate-300 text-sm flex-1 min-w-[120px] disabled:bg-slate-50"
                    aria-label="condition value"
                  />
                )}
                {!disabled && (
                  <button
                    type="button"
                    onClick={() => removeCondition(i)}
                    className="text-slate-400 hover:text-red-600 text-sm px-1"
                    aria-label="remove condition"
                  >
                    ✕
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}

      {!disabled && (
        <button
          type="button"
          onClick={addCondition}
          className="mt-2 text-xs text-primary-500 hover:text-primary-600 font-medium"
        >
          + Add condition
        </button>
      )}
    </div>
  )
}

/** Best-effort coerce a string input to number when it looks numeric, so codes
 *  round-trip as integers (the backend compares codes as ints). */
function coerce(raw: string): string | number {
  if (raw === '') return ''
  const n = Number(raw)
  return Number.isFinite(n) && raw.trim() !== '' ? n : raw
}
