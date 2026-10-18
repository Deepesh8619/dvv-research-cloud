import { QuestionCreate, QuestionOption } from '../../api/client'

/**
 * The add-question palette: one card per type a researcher actually reaches
 * for, in the order they meet them most. 'constant_sum' is the same UI-only
 * pseudo-type the review screen uses (labels/questionTypes.ts) — it creates a
 * plain numeric question with rows and a total, there is no wire type of its
 * own. Dropdown isn't a card — it's rare enough to reach via the type select
 * on an existing question instead of cluttering the palette.
 */
export const TYPE_CARDS: ReadonlyArray<{ key: string; hint: string }> = [
  { key: 'single', hint: 'One answer' },
  { key: 'multi', hint: 'Multiple answers' },
  { key: 'grid_single', hint: 'Rating grid' },
  { key: 'grid_multi', hint: 'Tick-box grid' },
  { key: 'rank', hint: 'Rank order' },
  { key: 'numeric', hint: 'Number' },
  { key: 'open_text', hint: 'Open text' },
  { key: 'constant_sum', hint: 'Adds up to' },
  { key: 'info', hint: 'Info screen' },
]

export interface NewQuestionSpec {
  create: Omit<QuestionCreate, 'qid'>
  /** constant_sum only — QuestionCreate has no validation field, so the total
      is patched in with a follow-up update right after the question exists. */
  validation?: Record<string, unknown>
}

/** A real starting shape for each card, not an empty shell — so the
    respondent preview shows something the moment a question lands. */
export function defaultQuestionFor(key: string): NewQuestionSpec {
  switch (key) {
    case 'single':
      return { create: { type: 'single', options: [{ code: 1, label: 'Yes' }, { code: 2, label: 'No' }] } }
    case 'multi':
      return {
        create: { type: 'multi', options: [{ code: 1, label: 'Option 1' }, { code: 2, label: 'Option 2' }] },
      }
    case 'grid_single':
      return {
        create: {
          type: 'grid_single',
          rows: [{ code: 1, label: 'Statement 1' }],
          cols: [{ code: 1, label: 'Disagree' }, { code: 2, label: 'Neutral' }, { code: 3, label: 'Agree' }],
        },
      }
    case 'grid_multi':
      return {
        create: {
          type: 'grid_multi',
          rows: [{ code: 1, label: 'Row 1' }],
          cols: [{ code: 1, label: 'Option 1' }, { code: 2, label: 'Option 2' }],
        },
      }
    case 'rank':
      return {
        create: {
          type: 'rank',
          options: [{ code: 1, label: 'Option 1' }, { code: 2, label: 'Option 2' }, { code: 3, label: 'Option 3' }],
        },
      }
    case 'open_text':
      return { create: { type: 'open_text' } }
    case 'constant_sum':
      return {
        create: { type: 'numeric', rows: [{ code: 1, label: 'Item 1' }, { code: 2, label: 'Item 2' }] },
        validation: { min: 0, max: 100, sum: 100 },
      }
    case 'info':
      return { create: { type: 'info', required: false } }
    case 'numeric':
    default:
      return { create: { type: 'numeric' } }
  }
}

// "1 Label" / "1. Label" / "1\tLabel" keep the code the source already gave
// them; anything else is treated as a bare label.
const PASTED_LINE = /^(\d+)[.)]?[ \t]+(.+)$/

/** Turns pasted lines into coded options in one step, instead of "+ Add
    option" clicked once per line. A bare line gets the next free code. */
export function parsePastedOptions(text: string): QuestionOption[] {
  let nextCode = 1
  const out: QuestionOption[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const m = PASTED_LINE.exec(line)
    if (m) {
      const code = Number(m[1])
      out.push({ code, label: m[2].trim() })
      nextCode = Math.max(nextCode, code + 1)
    } else {
      out.push({ code: nextCode, label: line })
      nextCode += 1
    }
  }
  return out
}
