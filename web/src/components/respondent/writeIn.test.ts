import { Model } from 'survey-core'
import { describe, expect, it } from 'vitest'

import {
  canContinue,
  emphasisePipes,
  groupWriteIns,
  onScreen,
  pipeTypedAnswer,
  pipedBold,
} from './writeIn'

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

describe('the clean look', () => {
  it('the box takes its words as a placeholder, not a title', () => {
    const json = { pages: [{ elements: [
      { type: 'radiogroup', name: 'Q1', choices: [98] },
      { type: 'text', name: 'Q1r98oe', title: 'Please specify' },
    ] }] }
    const panel = groupWriteIns(json).pages[0].elements[0] as unknown as { elements: Record<string, unknown>[] }
    expect(panel.elements[1].titleLocation).toBe('hidden')
    expect(panel.elements[1].placeholder).toBe('Please specify')
  })

  it('is ready only when what is on screen is answered, the Other box included', () => {
    const model = new Model(groupWriteIns({ pages: [{ elements: [
      { type: 'radiogroup', name: 'Q1', isRequired: true, choices: [1, 98] },
      { type: 'text', name: 'Q1r98oe', isRequired: true, visibleIf: '{Q1} = 98' },
      { type: 'text', name: 'Q2' },
    ] }] }))
    model.questionsOnPageMode = 'questionPerPage'
    expect(canContinue(model)).toBe(false)
    model.setValue('Q1', 1)
    expect(canContinue(model)).toBe(true)
    model.setValue('Q1', 98)
    expect(canContinue(model)).toBe(false)
    model.setValue('Q1r98oe', 'typed')
    expect(canContinue(model)).toBe(true)
    model.nextPage()
    expect(canContinue(model)).toBe(true) // optional
  })

  it('a grid waits for every statement', () => {
    const model = new Model({ pages: [{ elements: [{
      type: 'matrix', name: 'G', isRequired: true, columns: [1, 2], rows: ['a', 'b'],
    }] }] })
    model.questionsOnPageMode = 'questionPerPage'
    expect(canContinue(model)).toBe(false)
    model.setValue('G', { a: 1 })
    expect(canContinue(model)).toBe(false)
    model.setValue('G', { a: 1, b: 2 })
    expect(canContinue(model)).toBe(true)
  })

  it('bolds a piped answer and never lets typed text become markup', () => {
    const json = emphasisePipes({ pages: [{ elements: [{ type: 'text', name: 'Q2', title: 'You said {Q1}.' }] }] })
    expect(json.pages[0].elements[0].title).toBe('You said **{Q1}**.')
    const o: { text: string; html?: string } = { text: 'You said **<img src=x>**.' }
    pipedBold(null, o)
    expect(o.html).toBe('You said <b>&#60;img src=x&#62;</b>.')
    const empty: { text: string; html?: string } = { text: 'You said ****.' }
    pipedBold(null, empty)
    expect(empty.html).toBe('You said .')
    const plain: { text: string; html?: string } = { text: 'Nothing here' }
    pipedBold(null, plain)
    expect(plain.html).toBeUndefined()
  })
})

describe('quoting an Other answer', () => {
  const survey = (type: string) => {
    const model = new Model({ pages: [{ elements: [
      { type, name: 'Q1', choices: [{ value: 1, text: 'Xing' }, { value: 2, text: 'LinkedIn' }, { value: 98, text: 'Other (Specify)' }] },
      { type: 'text', name: 'Q1r98oe', visibleIf: type === 'checkbox' ? '{Q1} contains 98' : '{Q1} = 98' },
      { type: 'text', name: 'Q2', title: 'You said {Q1}.' },
    ] }] })
    model.onGetQuestionDisplayValue.add(pipeTypedAnswer)
    return model
  }
  const said = (m: Model) => m.getQuestionByName('Q2').locTitle.textOrHtml

  it('quotes what they typed on a single answer', () => {
    const m = survey('radiogroup')
    m.setValue('Q1', 98)
    m.setValue('Q1r98oe', 'Campus event')
    expect(said(m)).toBe('You said Campus event.')
  })

  it('keeps the label for a closed answer', () => {
    const m = survey('radiogroup')
    m.setValue('Q1', 1)
    expect(said(m)).toBe('You said Xing.')
  })

  it('mixes labels and typed text on a multi answer', () => {
    const m = survey('checkbox')
    m.setValue('Q1', [1, 98])
    m.setValue('Q1r98oe', 'Glassdoor')
    expect(said(m)).toBe('You said Xing, Glassdoor.')
  })

  it('falls back to the label when nothing was typed', () => {
    const m = survey('radiogroup')
    m.setValue('Q1', 98)
    expect(said(m)).toBe('You said Other (Specify).')
  })

  it('changing the typed text changes the sentence, and braces stay text', () => {
    const m = survey('radiogroup')
    m.setValue('Q1', 98)
    m.setValue('Q1r98oe', 'First')
    expect(said(m)).toBe('You said First.')
    m.setValue('Q1r98oe', '{Q1}')
    expect(said(m)).toBe('You said {Q1}.')
  })
})
