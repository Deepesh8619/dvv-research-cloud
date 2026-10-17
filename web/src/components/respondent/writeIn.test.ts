import { Model } from 'survey-core'
import { describe, expect, it } from 'vitest'

// The "Other (specify)" box the compiler emits after a question. The respondent
// page renders it with SurveyJS's own text question, so what matters is that the
// model shows it only when the option is picked and refuses an empty one.
const elements = (type: 'radiogroup' | 'checkbox', visibleIf: string) => [
  { type, name: 'Q1', choices: [{ value: 1, text: 'A' }, { value: 98, text: 'Other (Specify)' }] },
  { type: 'text', name: 'Q1r98oe', title: 'Please specify', isRequired: true, visibleIf },
]

describe('write-in text box', () => {
  it('opens for a single choice only when Other is picked, and is then required', () => {
    const model = new Model({ elements: elements('radiogroup', '{Q1} = 98') })
    const box = model.getQuestionByName('Q1r98oe')
    expect(box.isVisible).toBe(false)
    model.setValue('Q1', 98)
    expect(box.isVisible).toBe(true)
    expect(model.currentPage.hasErrors(false)).toBe(true) // empty: cannot continue
    model.setValue('Q1r98oe', 'Agender')
    expect(model.currentPage.hasErrors(false)).toBe(false)
    model.setValue('Q1', 1)
    expect(box.isVisible).toBe(false)
  })

  it('opens for a multi-select when Other is among the picks', () => {
    const model = new Model({ elements: elements('checkbox', '{Q1} contains 98') })
    const box = model.getQuestionByName('Q1r98oe')
    model.setValue('Q1', [1])
    expect(box.isVisible).toBe(false)
    model.setValue('Q1', [1, 98])
    expect(box.isVisible).toBe(true)
    expect(model.currentPage.hasErrors(false)).toBe(true)
  })

  it('does not hold up a respondent who never opened it', () => {
    const model = new Model({ elements: elements('radiogroup', '{Q1} = 98') })
    model.setValue('Q1', 1)
    expect(model.currentPage.hasErrors(false)).toBe(false)
  })
})
