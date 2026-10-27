import { createElement, useEffect, useReducer, useState } from 'react'
import type {
  ItemValue,
  MatrixRowModel,
  Question,
  QuestionMatrixModel,
  QuestionRankingModel,
  QuestionRatingModel,
} from 'survey-core'
import { Serializer } from 'survey-core'
import { ReactQuestionFactory, SurveyQuestionText } from 'survey-react-ui'

// The three answer controls a respondent spends the most time in, drawn by us
// instead of by SurveyJS. The MODEL stays SurveyJS's: every value is written
// through the question, so validation, row conditions, piping, the per-page
// save and the server's checks see exactly what they saw before. Only the
// drawing changes.
//
// Colour comes from the theme SurveyJS already carries (--sjs-primary-*), so a
// study's theme recolours these the same as everything else.

const ACCENT = 'var(--sjs-primary-backcolor, #4F46E5)'
const TINT = 'var(--sjs-primary-backcolor-light, rgba(79,70,229,0.1))'

/** Re-render when the question's answer changes, whoever changed it. */
function useAnswer(question: Question): unknown {
  const [, bump] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    // The survey's own event: the question's `value` property does not fire a
    // change for every question type, which left a stepper showing an empty
    // box over an answer the model already held.
    const survey = question.survey as unknown as {
      onValueChanged: { add: (f: (s: unknown, o: { name: string }) => void) => void; remove: (f: unknown) => void }
    } | null
    const onChange = (_: unknown, options: { name: string }) => {
      if (options.name === question.getValueName()) bump()
    }
    survey?.onValueChanged.add(onChange)
    return () => survey?.onValueChanged.remove(onChange)
  }, [question])
  return question.value
}

// ---------------------------------------------------------------- grid

/**
 * A grid, one statement at a time.
 *
 * Drawn as SurveyJS draws it, six statements on a five-point scale is thirty
 * stacked cards, and the respondent scrolls past the same five labels six
 * times — the page every soft launch loses people on. Here each statement gets
 * the screen to itself: pick, and the next slides in. The dots show what is
 * left and take you back to any statement to change it.
 */
/** How the compiler joins a bipolar row's two statements. */
const BIPOLAR = '  ⟷  '

export function MatrixCarousel({ question }: { question: QuestionMatrixModel }) {
  useAnswer(question)
  const rows: MatrixRowModel[] = question.visibleRows
  const columns: ItemValue[] = question.visibleColumns
  const firstOpen = rows.findIndex((r) => r.value == null)
  const [at, setAt] = useState(firstOpen < 0 ? 0 : firstOpen)
  if (rows.length === 0) return null
  const index = Math.min(at, rows.length - 1)
  const row = rows[index]
  // A scale reads best side by side from tablet width up — a label may wrap
  // inside its button. Phones, and long answer lists, stack.
  const inline = columns.length <= 7 && columns.every((c) => (c.text || '').length <= 24)

  // A multi-answer grid ("select all that apply" for each row): a row holds a
  // list of column codes and the respondent moves on when they are ready.
  const multi = Boolean((question as unknown as { multiAnswer?: boolean }).multiAnswer)
  const picks: unknown[] = Array.isArray(row?.value) ? row.value : []
  const toggle = (value: unknown) => {
    if (question.isReadOnly) return
    row.value = picks.includes(value) ? picks.filter((p) => p !== value) : [...picks, value]
  }

  const pick = (value: unknown) => {
    if (question.isReadOnly) return
    row.value = value
    const next = rows.findIndex((r, i) => i > index && r.value == null)
    const wrapped = next >= 0 ? next : rows.findIndex((r) => r.value == null)
    // Long enough to see the choice land, short enough to never feel slow.
    if (wrapped >= 0) window.setTimeout(() => setAt(wrapped), 260)
  }

  return (
    <div className="dvv-carousel">
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 tabular-nums">
          {index + 1} of {rows.length}
        </span>
        <div className="flex flex-wrap justify-end gap-1.5" role="tablist" aria-label="Statements">
          {rows.map((r, i) => (
            <button
              key={r.name}
              type="button"
              role="tab"
              aria-selected={i === index}
              aria-label={`${r.text}${r.value != null ? ' (answered)' : ''}`}
              onClick={() => setAt(i)}
              className="h-2.5 rounded-full transition-all duration-200"
              style={{
                width: i === index ? 22 : 10,
                background: i === index || r.value != null ? ACCENT : '#e2e8f0',
                opacity: i === index || r.value == null ? 1 : 0.45,
              }}
            />
          ))}
        </div>
      </div>

      <div key={row.name} className="dvv-slide rounded-2xl border border-slate-200 bg-slate-50/60 p-4 sm:p-5">
        {/* A bipolar row: two opposite statements, the scale between them. The
            side a statement sits on is the side its end of the scale is on. */}
        {(row.text || '').includes(BIPOLAR) ? (
          <div className="mb-4 grid grid-cols-2 gap-3" data-testid="bipolar-row">
            {(row.text || '').split(BIPOLAR).slice(0, 2).map((side, i) => (
              <p key={i} className={`rounded-xl bg-white p-3 text-[15px] font-semibold leading-snug text-slate-900 ring-1 ring-slate-200 ${i === 1 ? 'text-right' : ''}`}>
                {side}
              </p>
            ))}
          </div>
        ) : (
          <p className="mb-4 text-[17px] font-semibold leading-snug text-slate-900">{row.text}</p>
        )}
        {multi ? (
          <div role="group" aria-label={row.text} className="grid gap-2">
            {columns.map((c) => {
              const on = picks.includes(c.value)
              return (
                <button
                  key={String(c.value)}
                  type="button"
                  role="checkbox"
                  aria-checked={on}
                  onClick={() => toggle(c.value)}
                  className="dvv-choice flex min-h-[48px] items-center gap-3 rounded-xl border-[1.5px] px-3 py-2.5 text-left text-[15px] font-medium transition-all duration-150 active:scale-[0.98]"
                  style={{ borderColor: on ? ACCENT : '#e2e8f0', background: on ? '#eef2ff' : '#fff', color: '#1e293b' }}
                >
                  <span
                    aria-hidden
                    className="grid h-5 w-5 shrink-0 place-items-center rounded-md border-[1.5px] text-xs text-white"
                    style={{ borderColor: on ? ACCENT : '#cbd5e1', background: on ? ACCENT : '#fff' }}
                  >
                    {on ? '✓' : ''}
                  </span>
                  {c.text}
                </button>
              )
            })}
          </div>
        ) : isNumberScale(columns) ? (
          <NumberScale columns={columns} value={row.value} label={row.text} onPick={pick}
            oneRow={(row.text || '').includes(BIPOLAR)} />
        ) : (
        <div
          role="radiogroup"
          aria-label={row.text}
          className={inline ? 'grid gap-2 sm:grid-flow-col sm:auto-cols-fr' : 'grid gap-2'}
        >
          {columns.map((c, i) => {
            const on = row.value == c.value
            return (
              <button
                key={String(c.value)}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => pick(c.value)}
                className="dvv-choice min-h-[48px] rounded-xl leading-tight border-[1.5px] px-3 py-2.5 text-[15px] font-medium transition-all duration-150 active:scale-[0.98]"
                style={{
                  borderColor: on ? ACCENT : '#e2e8f0',
                  background: on ? ACCENT : '#fff',
                  color: on ? '#fff' : '#1e293b',
                }}
              >
                {isFeelingScale(columns) && <Face at={i} of={columns.length} />}
                {c.text}
              </button>
            )
          })}
        </div>
        )}
      </div>

      <div className="mt-3 flex justify-between text-sm">
        <button
          type="button"
          disabled={index === 0}
          onClick={() => setAt(index - 1)}
          className="rounded-lg px-2 py-1 font-medium text-slate-500 hover:text-slate-800 disabled:invisible"
        >
          ‹ Previous statement
        </button>
        <button
          type="button"
          disabled={index === rows.length - 1}
          onClick={() => setAt(index + 1)}
          className="rounded-lg px-2 py-1 font-medium text-slate-500 hover:text-slate-800 disabled:invisible"
        >
          Next statement ›
        </button>
      </div>
    </div>
  )
}

/** A short scale whose ends are feelings ("Very dissatisfied … Very satisfied"),
 * not a list of brands that happens to have five columns. */
export function isFeelingScale(columns: ItemValue[]): boolean {
  if (columns.length < 3 || columns.length > 7) return false
  const ends = `${columns[0].text} ${columns[columns.length - 1].text}`
  return /satisf|agree|likel|good|bad|poor|excellent|happy|appeal/i.test(ends)
}

/** A face per point, frown to smile. Hidden unless a template shows it (Play). */
function Face({ at, of }: { at: number; of: number }) {
  const mouth = 15 + (at / Math.max(of - 1, 1) - 0.5) * 8
  return (
    <svg className="dvv-face" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="10" />
      <circle cx="9" cy="9.5" r="0.6" fill="currentColor" />
      <circle cx="15" cy="9.5" r="0.6" fill="currentColor" />
      <path d={`M8 15 Q12 ${mouth} 16 15`} />
    </svg>
  )
}

/** 0-10 and the like: consecutive numbers, labelled only at a few points. */
function isNumberScale(columns: ItemValue[]): boolean {
  if (columns.length < 5) return false
  const values = columns.map((c) => Number(c.value))
  // Ascending, or descending: a bipolar grid's flipped half sees 10 … 0.
  const step = values[1] - values[0]
  if (Math.abs(step) !== 1) return false
  if (values.some((v, i) => Number.isNaN(v) || (i > 0 && v !== values[i - 1] + step))) return false
  const bare = columns.filter((c) => (c.text || '').trim() === String(c.value)).length
  return bare * 2 >= columns.length
}

/**
 * A scale as one row of numbers with its words underneath.
 *
 * Eleven full-width buttons, "0 … Somewhat more likely … 10", ran past the
 * bottom of a phone, and the statement being rated scrolled away with them:
 * the respondent was choosing a number for something they could no longer see.
 * The numbers fit in one or two rows; the labelled points become captions.
 */
function NumberScale({
  columns,
  value,
  label,
  onPick,
  ends,
  oneRow = false,
}: {
  columns: ItemValue[]
  value: unknown
  label: string
  onPick: (value: unknown) => void
  /** Words for the two ends when the points themselves carry none. */
  ends?: { min?: string; max?: string }
  /** Never wrap: a bipolar scale's left end must stay under the left statement. */
  oneRow?: boolean
}) {
  const worded = columns.filter((c) => (c.text || '').trim() !== String(c.value))
  const firstPoint = worded.find((c) => c === columns[0])
  const lastPoint = worded.find((c) => c === columns[columns.length - 1])
  const middle = worded.find((c) => c !== firstPoint && c !== lastPoint)
  const first = { text: firstPoint?.text || ends?.min }
  const last = { text: lastPoint?.text || ends?.max }
  return (
    <div>
      {/* Equal cells that wrap as whole rows: 0-10 is one row on a laptop and
          6 + 5 on a phone, never a lone "10" stretched across the width. */}
      <div
        role="radiogroup"
        aria-label={label}
        className="grid gap-1.5 [grid-template-columns:repeat(var(--phone),minmax(0,1fr))] sm:[grid-template-columns:repeat(var(--wide),minmax(0,1fr))]"
        style={{
          ['--phone' as string]: columns.length > 7 && !oneRow ? Math.ceil(columns.length / 2) : columns.length,
          ['--wide' as string]: columns.length,
        }}
      >
        {columns.map((c, i) => {
          const on = value == c.value
          return (
            <button
              key={String(c.value)}
              type="button"
              role="radio"
              aria-checked={on}
              aria-label={(c.text || '').trim() === String(c.value) ? String(c.value) : `${c.value} — ${c.text}`}
              onClick={() => onPick(c.value)}
              className={`dvv-choice h-12 rounded-xl border-[1.5px] font-semibold tabular-nums transition-all duration-150 active:scale-95 ${oneRow ? 'text-sm' : 'text-base'}`}
              style={{
                borderColor: on ? ACCENT : '#e2e8f0',
                background: on ? ACCENT : '#fff',
                color: on ? '#fff' : '#334155',
              }}
            >
              {columns.length <= 7 && <Face at={i} of={columns.length} />}
              {String(c.value)}
            </button>
          )
        })}
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-xs leading-snug text-slate-500">
        <span>{first?.text}</span>
        <span className="text-center">{middle ? `${middle.value} = ${middle.text}` : ''}</span>
        <span className="text-right">{last?.text}</span>
      </div>
    </div>
  )
}

/**
 * One thing rated 0-10 ("How do you feel about Bubble now?"): the same number
 * pad as a grid statement, so a respondent learns it once.
 */
export function RatingPad({ question }: { question: QuestionRatingModel }) {
  const value = useAnswer(question)
  const columns = question.visibleRateValues as ItemValue[]
  return (
    <NumberScale
      columns={columns}
      value={value}
      label={question.title}
      onPick={(v) => !question.isReadOnly && (question.value = v)}
      ends={{ min: question.minRateDescription, max: question.maxRateDescription }}
    />
  )
}

// ---------------------------------------------------------------- rank

/**
 * Rank by tapping, favourite first.
 *
 * Dragging a list of eight on a phone is the worst interaction in a survey:
 * the page scrolls when you meant to drag, and the item you let go of lands one
 * slot off. Tapping in order is the same decision without the motor skill. The
 * last one left is placed for you, since there is nowhere else for it to go.
 */
export function TapToRank({ question }: { question: QuestionRankingModel }) {
  const value = useAnswer(question)
  const ranked: unknown[] = Array.isArray(value) ? value : []
  const choices: ItemValue[] = question.visibleChoices
  const byValue = new Map(choices.map((c) => [String(c.value), c]))
  const rankedItems = ranked.map((v) => byValue.get(String(v))).filter(Boolean) as ItemValue[]
  const open = choices.filter((c) => !ranked.some((v) => String(v) === String(c.value)))

  const set = (next: unknown[]) => {
    if (question.isReadOnly) return
    question.value = next.length ? next : undefined
  }
  const add = (c: ItemValue) => {
    const next = [...ranked, c.value]
    const rest = open.filter((o) => o !== c)
    set(rest.length === 1 ? [...next, rest[0].value] : next)
  }
  const move = (i: number, by: number) => {
    const next = [...ranked]
    ;[next[i], next[i + by]] = [next[i + by], next[i]]
    set(next)
  }

  return (
    <div className="space-y-5">
      <ol className="space-y-2" aria-label="Your ranking">
        {rankedItems.map((c, i) => (
          <li
            key={String(c.value)}
            className="dvv-slide flex items-center gap-3 rounded-xl border-[1.5px] bg-white px-3 py-2.5"
            style={{ borderColor: ACCENT, background: TINT }}
          >
            <span
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white tabular-nums"
              style={{ background: ACCENT }}
            >
              {i + 1}
            </span>
            <span className="flex-1 text-[15px] font-medium text-slate-900">{c.text}</span>
            <span className="flex shrink-0 gap-1">
              <IconButton label={`Move ${c.text} up`} disabled={i === 0} onClick={() => move(i, -1)}>↑</IconButton>
              <IconButton label={`Move ${c.text} down`} disabled={i === rankedItems.length - 1} onClick={() => move(i, 1)}>↓</IconButton>
              <IconButton label={`Remove ${c.text}`} onClick={() => set(ranked.filter((v) => v !== c.value))}>×</IconButton>
            </span>
          </li>
        ))}
        {rankedItems.length === 0 && (
          <li className="rounded-xl border-[1.5px] border-dashed border-slate-300 px-4 py-4 text-center text-sm text-slate-500">
            Tap your favourite first — it becomes #1
          </li>
        )}
      </ol>

      {open.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            {rankedItems.length ? `Next: #${rankedItems.length + 1}` : 'Tap to rank'}
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {open.map((c) => (
              <button
                key={String(c.value)}
                type="button"
                onClick={() => add(c)}
                className="dvv-choice flex min-h-[48px] items-center gap-3 rounded-xl border-[1.5px] border-slate-200 bg-white px-3 py-2.5 text-left text-[15px] text-slate-800 transition-all duration-150 hover:border-slate-300 active:scale-[0.98]"
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-[1.5px] border-dashed border-slate-300 text-sm text-slate-400">
                  +
                </span>
                {c.text}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function IconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-white hover:text-slate-900 disabled:opacity-25"
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------- number

function bounds(question: Question): { min?: number; max?: number } {
  const v = (question.validators || []).find((x) => x.getType() === 'numericvalidator') as
    | { minValue?: number; maxValue?: number }
    | undefined
  return { min: v?.minValue ?? undefined, max: v?.maxValue ?? undefined }
}

/**
 * A count, with a minus and a plus a thumb can hit.
 *
 * Most numeric questions are small counts — products used, people in the
 * household — and a phone keyboard is a heavy way to enter "2". The box in the
 * middle still takes typing, for the ages and amounts that are not small.
 */
export function NumberStepper({ question }: { question: Question }) {
  const value = useAnswer(question)
  const { min, max } = bounds(question)
  // "99: Prefer not to answer" — stored as its code, shown as a choice, never
  // as a number in the box.
  const optOuts: ItemValue[] = (question as unknown as { optOuts?: ItemValue[] }).optOuts ?? []
  const declined = optOuts.find((o) => value != null && String(o.value) === String(value))
  const n = declined || value === undefined || value === null || value === '' ? undefined : Number(value)
  const clamp = (x: number) => Math.min(max ?? Infinity, Math.max(min ?? -Infinity, x))
  const step = (by: number) => {
    if (question.isReadOnly) return
    // The first tap lands on a real answer — 1, or the minimum if higher — not
    // on a 0 that looks like the tap did nothing.
    if (n === undefined) question.value = clamp(by > 0 ? Math.max(min ?? 1, 1) : min ?? 0)
    else question.value = clamp(n + by)
  }
  return (
    <div>
    <div className="flex items-stretch gap-3" style={{ opacity: declined ? 0.45 : 1 }}>
      <StepButton label="Decrease" disabled={n !== undefined && min !== undefined && n <= min} onClick={() => step(-1)}>
        −
      </StepButton>
      <input
        type="number"
        inputMode="numeric"
        aria-label={question.title}
        value={n ?? ''}
        min={min}
        max={max}
        readOnly={question.isReadOnly}
        onChange={(e) => (question.value = e.target.value === '' ? undefined : Number(e.target.value))}
        className="min-w-0 flex-1 rounded-xl border-[1.5px] border-slate-200 bg-white text-center text-2xl font-semibold text-slate-900 tabular-nums focus:outline-none"
        style={{ height: 56, borderColor: n !== undefined ? ACCENT : undefined }}
      />
      <StepButton label="Increase" disabled={n !== undefined && max !== undefined && n >= max} onClick={() => step(1)}>
        +
      </StepButton>
    </div>
    {optOuts.length > 0 && (
      <div className="mt-3 flex flex-wrap gap-2">
        {optOuts.map((o) => {
          const on = declined === o
          return (
            <button
              key={String(o.value)}
              type="button"
              role="checkbox"
              aria-checked={on}
              onClick={() => !question.isReadOnly && (question.value = on ? undefined : o.value)}
              className="rounded-full border-[1.5px] px-4 py-2 text-sm font-medium transition-colors"
              style={{
                borderColor: on ? ACCENT : '#e2e8f0',
                background: on ? TINT : '#fff',
                color: on ? '#0f172a' : '#475569',
              }}
            >
              {o.text}
            </button>
          )
        })}
      </div>
    )}
    </div>
  )
}

function StepButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="w-16 shrink-0 rounded-xl bg-slate-100 text-2xl font-medium text-slate-700 transition-all duration-150 hover:bg-slate-200 active:scale-95 disabled:opacity-30"
      style={{ height: 56 }}
    >
      {children}
    </button>
  )
}

// ---------------------------------------------------------------- constant sum

/** The total a constant-sum question must reach, from its own validator. */
function totalOf(question: Question): number | null {
  for (const v of question.validators || []) {
    const m = /=\s*(\d+)\s*$/.exec((v as unknown as { expression?: string }).expression || '')
    if (m) return Number(m[1])
  }
  return null
}

/**
 * Shares that must add up — "what percentage goes to each?" — as one row per
 * item and a running total that fills as they type. The rule is visible while
 * they answer, not discovered as an error when they press Next.
 */
export function ConstantSum({ question }: { question: Question }) {
  const value = (useAnswer(question) as Record<string, number> | undefined) ?? {}
  const items = (question as unknown as { items: Array<{ name: string; title: string }> }).items
  const total = totalOf(question)
  const sum = items.reduce((acc, it) => acc + (Number(value[it.name]) || 0), 0)
  const done = total !== null && sum === total
  const over = total !== null && sum > total
  const set = (name: string, raw: string) => {
    if (question.isReadOnly) return
    const next = { ...value }
    if (raw === '') delete next[name]
    else next[name] = Math.max(0, Number(raw))
    question.value = Object.keys(next).length ? next : undefined
  }
  return (
    // SurveyJS sets nowrap on this question type; the labels ran off the card.
    <div className="space-y-2.5 whitespace-normal">
      {items.map((it) => (
        <label
          key={it.name}
          className="flex items-center gap-3 rounded-xl border-[1.5px] border-slate-200 bg-white px-3 py-2.5 focus-within:border-[color:var(--sjs-primary-backcolor)]"
        >
          <span className="min-w-0 flex-1 text-[15px] leading-snug text-slate-800">{it.title}</span>
          <span className="flex shrink-0 items-center gap-1">
            <input
              type="number"
              inputMode="numeric"
              min={0}
              max={total ?? undefined}
              aria-label={it.title}
              value={value[it.name] ?? ''}
              onChange={(e) => set(it.name, e.target.value)}
              className="h-11 w-16 rounded-lg border border-slate-200 text-center text-lg font-semibold tabular-nums text-slate-900 focus:outline-none"
            />
            {total === 100 && <span className="text-sm font-medium text-slate-500">%</span>}
          </span>
        </label>
      ))}
      {total !== null && (
        <div className="pt-1" aria-live="polite">
          <div className="h-2 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{
                width: `${Math.min(100, (sum / total) * 100)}%`,
                background: done ? '#10b981' : over ? '#ef4444' : ACCENT,
              }}
            />
          </div>
          <p className={`mt-1.5 text-sm font-medium tabular-nums ${done ? 'text-emerald-600' : over ? 'text-red-600' : 'text-slate-500'}`}>
            {done ? `Adds up to ${total} ✓` : over ? `${sum} — that is ${sum - total} over ${total}` : `${sum} of ${total}`}
          </p>
        </div>
      )}
    </div>
  )
}

let registered = false

/** Swap SurveyJS's renderers for ours. Idempotent; the respondent page calls it. */
export function registerRespondentControls(): void {
  if (registered) return
  registered = true
  // The compiler emits a number box's opt-out codes as `optOuts`; unregistered,
  // SurveyJS drops the property on load.
  if (!Serializer.findProperty('text', 'optOuts')) Serializer.addProperty('text', { name: 'optOuts:itemvalues' })
  // A multi-answer grid is compiled as a matrix flagged `multiAnswer`.
  if (!Serializer.findProperty('matrix', 'multiAnswer')) Serializer.addProperty('matrix', { name: 'multiAnswer:boolean' })
  const factory = ReactQuestionFactory.Instance
  factory.registerQuestion('matrix', ((props: { question: QuestionMatrixModel }) =>
    createElement(MatrixCarousel, props)) as never)
  factory.registerQuestion('multipletext', ((props: { question: Question }) =>
    createElement(ConstantSum, props)) as never)
  factory.registerQuestion('rating', ((props: { question: QuestionRatingModel }) =>
    createElement(RatingPad, props)) as never)
  factory.registerQuestion('ranking', ((props: { question: QuestionRankingModel }) =>
    createElement(TapToRank, props)) as never)
  factory.registerQuestion('text', ((props: { question: Question }) =>
    props.question.inputType === 'number'
      ? createElement(NumberStepper, props)
      : createElement(SurveyQuestionText, props as never)) as never)
}
