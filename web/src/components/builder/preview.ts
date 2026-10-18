import { Question, QuestionOption } from '../../api/client'

function choices(options: QuestionOption[]): Array<{ value: number; text: string }> {
  return options.map((o) => ({ value: o.code, text: o.label }))
}

/** 0-10 style scale: consecutive codes, labelled at only a few points — mirrors
    src/renderers/surveyjs_compiler.py::_is_numeric_scale. */
function isNumericScale(options: QuestionOption[]): boolean {
  if (options.length < 5) return false
  const codes = options.map((o) => o.code)
  if (codes.some((c, i) => i > 0 && c !== codes[i - 1] + 1)) return false
  const bare = options.filter((o) => (o.label || '').trim() === String(o.code)).length
  return bare * 2 >= options.length
}

/**
 * One question -> its SurveyJS element. A trimmed mirror of the same type
 * mapping as src/renderers/surveyjs_compiler.py::_compile_question — routing
 * (visibleIf), masks and pipe *resolution* are left out on purpose: this is a
 * draft preview, not a respondent run, and a piping marker the researcher
 * typed (`insertPiping` in SurveyBuilderPage.tsx) is already the literal
 * `{QID}` text SurveyJS interpolates, so there's nothing left to resolve.
 */
export function compileQuestionForPreview(q: Question): Record<string, unknown> {
  if (q.type === 'info') return { type: 'html', name: q.qid, html: q.text || ' ' }

  const el: Record<string, unknown> = {
    name: q.qid,
    title: q.text || `(${q.qid} has no text yet)`,
    isRequired: q.required,
  }

  switch (q.type) {
    case 'single':
      if (isNumericScale(q.options)) {
        el.type = 'rating'
        el.rateValues = q.options.map((o) => ({ value: o.code, text: String(o.code) }))
        el.displayMode = 'buttons'
      } else {
        el.type = 'radiogroup'
        el.choices = choices(q.options)
      }
      break
    case 'multi':
      el.type = 'checkbox'
      el.choices = choices(q.options)
      break
    case 'dropdown':
      el.type = 'dropdown'
      el.choices = choices(q.options)
      break
    case 'rank':
      el.type = 'ranking'
      el.choices = choices(q.options)
      break
    case 'grid_single':
    case 'grid_multi':
      el.type = 'matrix'
      el.rows = choices(q.rows ?? [])
      el.columns = choices(q.cols ?? [])
      if (q.type === 'grid_multi') el.multiAnswer = true
      break
    case 'numeric':
      if (q.rows && q.rows.length > 0) {
        // Constant sum: one box per item — see ConstantSum in
        // components/respondent/controls.tsx, which is what actually draws it.
        el.type = 'multipletext'
        el.items = q.rows.map((r) => ({ name: String(r.code), title: r.label, inputType: 'number' }))
        const total = q.validation?.sum
        if (typeof total === 'number') {
          const summed = q.rows.map((r) => `{${q.qid}.${r.code}}`).join(' + ')
          el.validators = [
            { type: 'expression', expression: `${summed} = ${total}`, text: `These should add up to ${total}.` },
          ]
        }
      } else {
        el.type = 'text'
        el.inputType = 'number'
      }
      break
    case 'open_text':
      el.type = 'comment'
      break
    default:
      el.type = 'text'
  }

  return el
}

/** A one-page SurveyJS model for however many questions are handed in — the
    same function builds a single-question preview and the whole-draft one. */
export function buildPreviewJson(questions: Question[]): Record<string, unknown> {
  return {
    elements: questions.map(compileQuestionForPreview),
    showQuestionNumbers: 'off',
    showNavigationButtons: false,
    showCompleteButton: false,
  }
}
