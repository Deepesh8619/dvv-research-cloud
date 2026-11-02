import { Model } from 'survey-core'
import { describe, expect, it } from 'vitest'

import { groupWriteIns, onScreen } from './writeIn'

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

describe('the box on the same screen', () => {
  const json = () => ({
    pages: [{
      name: 'B',
      elements: [
        { type: 'text', name: 'Q0' },
        { type: 'radiogroup', name: 'Q1', isRequired: true, choices: [
          { value: 1, text: 'A' }, { value: 98, text: 'Other (Specify)' }, { value: 99, text: 'Other two (Specify)' }] },
        { type: 'text', name: 'Q1r98oe', isRequired: true, visibleIf: '{Q1} = 98' },
        { type: 'text', name: 'Q1r99oe', isRequired: true, visibleIf: '{Q1} = 99' },
        { type: 'text', name: 'Q2' },
      ],
    }],
  })

  it('groups a question with its boxes and leaves others alone', () => {
    const page = groupWriteIns(json()).pages[0]
    expect(page.elements.map((e) => e.name)).toEqual(['Q0', 'Q1_with_write_in', 'Q2'])
    expect((page.elements[1] as unknown as { elements: { name: string }[] }).elements.map((e) => e.name))
      .toEqual(['Q1', 'Q1r98oe', 'Q1r99oe'])
    const plain = { pages: [{ elements: [{ type: 'text', name: 'A' }] }] }
    expect(groupWriteIns(plain).pages[0].elements[0].name).toBe('A')
  })

  it('opens on the screen of the answer, refuses an empty box, and keeps the data column', () => {
    const model = new Model(groupWriteIns(json()))
    model.questionsOnPageMode = 'questionPerPage'
    model.setValue('Q0', 'x')
    model.nextPage()
    expect(onScreen(model)?.name).toBe('Q1')
    expect(model.getQuestionByName('Q1r98oe').isVisible).toBe(false)
    model.setValue('Q1', 98)
    expect(model.getQuestionByName('Q1r98oe').isVisible).toBe(true)
    expect(model.nextPage()).toBe(false) // empty: stays here
    expect(onScreen(model)?.name).toBe('Q1')
    model.setValue('Q1r98oe', 'typed')
    expect(model.nextPage()).toBe(true)
    expect(onScreen(model)?.name).toBe('Q2')
    expect(model.data.Q1r98oe).toBe('typed')
  })

  it('a closed answer moves straight on', () => {
    const model = new Model(groupWriteIns(json()))
    model.questionsOnPageMode = 'questionPerPage'
    model.setValue('Q0', 'x')
    model.nextPage()
    model.setValue('Q1', 1)
    expect(model.nextPage()).toBe(true)
    expect(onScreen(model)?.name).toBe('Q2')
  })

  it('a question whose base is not met skips its whole panel', () => {
    const j = json()
    ;(j.pages[0].elements[1] as Record<string, unknown>).visibleIf = '{Q0} = "yes"'
    const model = new Model(groupWriteIns(j))
    model.questionsOnPageMode = 'questionPerPage'
    model.setValue('Q0', 'no')
    expect(model.nextPage()).toBe(true)
    expect(onScreen(model)?.name).toBe('Q2')
  })
})
