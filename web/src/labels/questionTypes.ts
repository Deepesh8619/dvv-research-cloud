/**
 * Plain-language names for question types.
 *
 * The stored values are engine identifiers ("grid_single", "open_text") and the
 * UI was rendering them raw, which asks a researcher to learn our vocabulary
 * before they can review anything. The wire format does not change — only what
 * a human reads. One map, used by every screen that shows or picks a type, so
 * the review page and the builder can never drift apart.
 */
export interface TypeLabel {
  /** What the reviewer sees. */
  label: string
  /** One line describing what the respondent actually does. */
  hint: string
}

export const QUESTION_TYPE_LABELS: Record<string, TypeLabel> = {
  single: { label: 'One answer', hint: 'Respondent picks exactly one option.' },
  multi: { label: 'Multiple answers', hint: 'Respondent can tick as many options as apply.' },
  dropdown: { label: 'Dropdown list', hint: 'One answer, chosen from a long list.' },
  rank: { label: 'Ranking', hint: 'Respondent puts the options in order of preference.' },
  grid_single: {
    label: 'Rating grid',
    hint: 'Each row is rated on the same scale — one answer per row.',
  },
  numeric: { label: 'Number', hint: 'Respondent types a number, e.g. an age or a count.' },
  open_text: { label: 'Written answer', hint: 'Respondent types their answer in their own words.' },
  info: {
    label: 'Not a question',
    hint: 'Shown as text, or computed by the scripter — nothing is asked here.',
  },
  // UI-only: not a wire type. Saved as numeric + rows + validation.sum (see
  // src/engines/questionnaire/constant_sum.py) — this only lets the editor
  // offer it as one choice instead of "pick numeric, then go add rows by hand".
  constant_sum: {
    label: 'Adds up to (constant sum)',
    hint: 'Respondent splits a number across items so they add up to a fixed total.',
  },
}

export function typeLabel(type: string | null | undefined): string {
  return QUESTION_TYPE_LABELS[type ?? '']?.label ?? type ?? '—'
}

export function typeHint(type: string | null | undefined): string {
  return QUESTION_TYPE_LABELS[type ?? '']?.hint ?? ''
}
