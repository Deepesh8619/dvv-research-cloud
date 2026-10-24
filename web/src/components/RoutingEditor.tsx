import { useState } from 'react'

import { Expression, ExprOp, Question } from '../api/client'

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

/** What a condition may refer to, in the researcher's words. */
export type RoutingQuestion = Pick<Question, 'qid' | 'text' | 'type' | 'options'>

const CHOICE = new Set(['single', 'dropdown'])
// Verbs that fit the question: a single choice "is", a multi-select "includes",
// a number "is more than". Anything else keeps the generic list.
function verbsFor(type: string | undefined): Array<{ value: ExprOp; label: string }> {
  if (type && CHOICE.has(type)) return [{ value: 'eq', label: 'is' }, { value: 'ne', label: 'is not' }, { value: 'in', label: 'is any of' }]
  if (type === 'multi') return [{ value: 'selected', label: 'includes' }]
  if (type === 'numeric') return [{ value: 'eq', label: 'is' }, { value: 'gt', label: 'is more than' }, { value: 'lt', label: 'is less than' }]
  return LEAF_OPS
}

/** "Do you drink coffee? is Yes" — a condition read back in words. */
export function describeCondition(c: Expression, questions: RoutingQuestion[]): string {
  const q = questions.find((x) => x.qid === c.qid)
  if (!q) return ''
  const label = (v: unknown) => q.options.find((o) => o.code === v)?.label ?? String(v ?? '')
  const verb = verbsFor(q.type).find((o) => o.value === c.op)?.label ?? c.op
  const what = c.op === 'in' ? (c.values ?? []).map(label).join(' or ') : label(c.value)
  return `${q.text.length > 70 ? q.text.slice(0, 69) + '…' : q.text} ${verb} ${what || '…'}`
}

interface Props {
  /** The questions a condition may name, with their answers: turns each condition
      into a sentence picked from labels instead of codes typed by hand. */
  questions?: RoutingQuestion[]
  label: string
  flat: FlatRouting
  onChange: (flat: FlatRouting) => void
  qidOptions: string[] // prior qids available to reference
  disabled?: boolean
  /** Shown when there are no conditions. "Always shown" is wrong under a
      terminate rule, so each caller says what empty means for it. */
  emptyText?: string
}

export default function RoutingEditor({ label, flat, onChange, qidOptions, disabled, emptyText, questions }: Props) {
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

  // What the user is currently TYPING in a codes box, keyed by row.
  //
  // The input cannot be driven straight from `cond.values`: that array is the
  // parsed result, so `join(', ')` erases the separator the moment it is typed
  // — "1," parses to [1] and renders back as "1", making a comma impossible to
  // enter and the field effectively single-value. Holding the raw text while
  // the box has focus lets commas, spaces and half-typed codes survive; the
  // parsed array is still updated on every keystroke, so nothing is deferred.
  const [codeDrafts, setCodeDrafts] = useState<Record<number, string>>({})

  const clearDraft = (i: number) =>
    setCodeDrafts((drafts) => {
      const next = { ...drafts }
      delete next[i]
      return next
    })

  const setCondition = (i: number, patch: Partial<Expression>) => {
    const conditions = flat.conditions.map((c, idx) => (idx === i ? { ...c, ...patch } : c))
    onChange({ ...flat, conditions })
  }

  const removeCondition = (i: number) => {
    // Drafts are keyed by row index, so a removal would leave the draft of the
    // deleted row sitting on whichever row slid into its place.
    setCodeDrafts({})
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
            <option value="and">all of these</option>
            <option value="or">any of these</option>
          </select>
        )}
      </div>

      {questions && flat.conditions.some((c) => describeCondition(c, questions)) && (
        <p className="mb-2 text-sm text-slate-700">
          {flat.conditions.map((c) => describeCondition(c, questions)).filter(Boolean)
            .join(flat.connective === 'and' ? ', and ' : ', or ')}
        </p>
      )}
      {flat.conditions.length === 0 ? (
        <p className="text-sm text-slate-400 mb-2">{emptyText ?? 'No condition — always shown.'}</p>
      ) : (
        <div className="space-y-2">
          {flat.conditions.map((cond, i) => {
            const usesValues = cond.op === 'in'
            const known = questions?.filter((q) => qidOptions.includes(q.qid))
            const q = known?.find((x) => x.qid === cond.qid)
            if (known && (!cond.qid || q)) {
              const verbs = verbsFor(q?.type)
              const choices = q && q.options.length > 0 && q.type !== 'numeric'
              return (
                <div key={i} className="flex flex-wrap items-center gap-2 rounded-md bg-slate-50 p-2">
                  <select value={cond.qid ?? ''} disabled={disabled} aria-label="condition question"
                    className="h-8 max-w-full rounded-md border border-slate-300 px-2 text-sm"
                    onChange={(e) => {
                      const next = known.find((x) => x.qid === e.target.value)
                      setCondition(i, { qid: e.target.value, op: verbsFor(next?.type)[0].value, value: '', values: undefined })
                    }}>
                    <option value="">pick a question…</option>
                    {known.map((x) => (
                      <option key={x.qid} value={x.qid}>{x.qid} — {x.text.length > 60 ? x.text.slice(0, 59) + '…' : x.text}</option>
                    ))}
                  </select>
                  {q && (
                    <select value={cond.op} disabled={disabled} aria-label="condition operator"
                      className="h-8 rounded-md border border-slate-300 px-2 text-sm"
                      onChange={(e) => {
                        const op = e.target.value as ExprOp
                        if (op === 'in') setCondition(i, { op, values: cond.value !== '' && cond.value != null ? [cond.value] : [], value: undefined })
                        else setCondition(i, { op, value: cond.values?.[0] ?? '', values: undefined })
                      }}>
                      {verbs.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  )}
                  {q && choices && usesValues && (
                    <span className="flex flex-wrap gap-1" role="group" aria-label="condition answers">
                      {q.options.map((o) => {
                        const on = (cond.values ?? []).includes(o.code)
                        return (
                          <button key={o.code} type="button" disabled={disabled} aria-pressed={on}
                            onClick={() => setCondition(i, { values: on ? (cond.values ?? []).filter((v) => v !== o.code) : [...(cond.values ?? []), o.code] })}
                            className={`rounded-full border px-2.5 py-0.5 text-xs ${on ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300 bg-white text-slate-700'}`}>
                            {o.label}
                          </button>
                        )
                      })}
                    </span>
                  )}
                  {q && choices && !usesValues && (
                    <select value={cond.value == null ? '' : String(cond.value)} disabled={disabled} aria-label="condition answer"
                      className="h-8 max-w-xs rounded-md border border-slate-300 px-2 text-sm"
                      onChange={(e) => setCondition(i, { value: coerce(e.target.value) })}>
                      <option value="">pick an answer…</option>
                      {q.options.map((o) => <option key={o.code} value={o.code}>{o.label}</option>)}
                    </select>
                  )}
                  {q && !choices && (
                    <input value={cond.value == null ? '' : String(cond.value)} disabled={disabled} inputMode="decimal"
                      aria-label="condition value" placeholder={q.type === 'numeric' ? 'a number' : 'value'}
                      className="h-8 w-28 rounded-md border border-slate-300 px-2 text-sm"
                      onChange={(e) => setCondition(i, { value: coerce(e.target.value) })} />
                  )}
                  {!disabled && (
                    <button type="button" onClick={() => removeCondition(i)} aria-label="remove condition"
                      className="ml-auto px-1 text-sm text-slate-400 hover:text-red-600">✕</button>
                  )}
                </div>
              )
            }
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
                    value={codeDrafts[i] ?? (cond.values ?? []).join(', ')}
                    disabled={disabled}
                    onChange={(e) => {
                      const raw = e.target.value
                      setCodeDrafts((drafts) => ({ ...drafts, [i]: raw }))
                      setCondition(i, {
                        values: raw
                          .split(',')
                          .map((s) => coerce(s.trim()))
                          .filter((v) => v !== ''),
                      })
                    }}
                    // On blur the box goes back to showing the parsed codes, so
                    // stray separators ("1,,2 ,") are tidied to "1, 2" and what
                    // is displayed is exactly what will be saved.
                    onBlur={() => clearDraft(i)}
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
