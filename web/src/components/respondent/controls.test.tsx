import { act, fireEvent, render, screen } from '@testing-library/react'
import { Model } from 'survey-core'
import type { Question, QuestionMatrixModel, QuestionRankingModel } from 'survey-core'
import { describe, expect, it, vi } from 'vitest'

import type { QuestionRatingModel } from 'survey-core'

import { respondentTitle } from '../../pages/RespondentPage'
import { ConstantSum, MatrixCarousel, NumberStepper, RatingPad, TapToRank, registerRespondentControls } from './controls'

registerRespondentControls() // registers the optOuts property

// Against a REAL SurveyJS model: the point of these controls is that they write
// through the model, so the answer the server receives is the model's.
function survey(element: Record<string, unknown>) {
  const model = new Model({ elements: [{ name: 'Q', ...element }] })
  return { model, question: model.getQuestionByName('Q') }
}

describe('TapToRank', () => {
  it('ranks in the order tapped and places the last one itself', () => {
    const { model, question } = survey({ type: 'ranking', choices: ['A', 'B', 'C'] })
    ;(question as unknown as { selectToRankEnabled: boolean }).selectToRankEnabled = true
    render(<TapToRank question={question as QuestionRankingModel} />)
    fireEvent.click(screen.getByRole('button', { name: /C/ }))
    expect(model.getValue('Q')).toEqual(['C'])
    fireEvent.click(screen.getByRole('button', { name: /A/ }))
    // Only B was left, so there was nowhere else for it to go.
    expect(model.getValue('Q')).toEqual(['C', 'A', 'B'])
  })

  it('moves an item up and takes one back out', () => {
    const { model, question } = survey({ type: 'ranking', choices: ['A', 'B', 'C'] })
    model.setValue('Q', ['A', 'B', 'C'])
    render(<TapToRank question={question as QuestionRankingModel} />)
    fireEvent.click(screen.getByLabelText('Move C up'))
    expect(model.getValue('Q')).toEqual(['A', 'C', 'B'])
    fireEvent.click(screen.getByLabelText('Remove A'))
    expect(model.getValue('Q')).toEqual(['C', 'B'])
  })
})

describe('NumberStepper', () => {
  it('starts at 1, not a 0 that looks like nothing happened, and respects the bounds', () => {
    const { model, question } = survey({
      type: 'text', inputType: 'number', validators: [{ type: 'numeric', minValue: 0, maxValue: 2 }],
    })
    render(<NumberStepper question={question as Question} />)
    const plus = screen.getByLabelText('Increase')
    fireEvent.click(plus)
    expect(model.getValue('Q')).toBe(1)
    fireEvent.click(plus)
    fireEvent.click(plus) // past the maximum
    expect(model.getValue('Q')).toBe(2)
    expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('2')
  })
})

describe('NumberStepper opt-out', () => {
  it('stores the code, empties the box, and typing clears it', () => {
    const { model, question } = survey({
      type: 'text', inputType: 'number', optOuts: [{ value: 99, text: 'Prefer not to answer' }],
    })
    render(<NumberStepper question={question as Question} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Prefer not to answer' }))
    expect(model.getValue('Q')).toBe(99)
    // 99 is a choice here, not an age: the box stays empty.
    expect((screen.getByRole('spinbutton') as HTMLInputElement).value).toBe('')
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '31' } })
    expect(model.getValue('Q')).toBe(31)
    expect(screen.getByRole('checkbox').getAttribute('aria-checked')).toBe('false')
  })
})

describe('MatrixCarousel', () => {
  it('writes the row answer and moves to the next unanswered statement', () => {
    vi.useFakeTimers()
    const { model, question } = survey({
      type: 'matrix', rows: [{ value: 1, text: 'Price' }, { value: 2, text: 'Taste' }],
      columns: [{ value: 1, text: 'Low' }, { value: 2, text: 'High' }],
    })
    render(<MatrixCarousel question={question as QuestionMatrixModel} />)
    expect(screen.getByText('Price')).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: 'High' }))
    expect(model.getValue('Q')).toEqual({ 1: 2 })
    act(() => vi.runAllTimers())
    expect(screen.getByText('Taste')).toBeTruthy()
    expect(screen.getByText('2 of 2')).toBeTruthy()
    vi.useRealTimers()
  })
})

const ZERO_TEN = Array.from({ length: 11 }, (_, i) => ({
  value: i,
  text: i === 0 ? 'Very Unlikely' : i === 5 ? 'Might or Might Not' : i === 10 ? 'Very Likely' : String(i),
}))

describe('a 0-10 scale', () => {
  it('is a number pad inside a grid statement, with its words underneath', () => {
    const { model, question } = survey({
      type: 'matrix', rows: [{ value: 1, text: 'Bubble' }], columns: ZERO_TEN,
    })
    render(<MatrixCarousel question={question as QuestionMatrixModel} />)
    // Numbers on the buttons, not eleven full-width labels.
    expect(screen.getByRole('radio', { name: '7' })).toBeTruthy()
    expect(screen.getByText('Very Unlikely')).toBeTruthy()
    expect(screen.getByText('5 = Might or Might Not')).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: '7' }))
    expect(model.getValue('Q')).toEqual({ 1: 7 })
  })

  it('puts a bipolar pair on either side of the scale', () => {
    const { model, question } = survey({
      type: 'matrix', rows: [{ value: 1, text: 'I stick with brands I know  ⟷  I look for new ones' }],
      columns: ZERO_TEN,
    })
    render(<MatrixCarousel question={question as QuestionMatrixModel} />)
    expect(screen.getByTestId('bipolar-row').textContent).toBe('I stick with brands I knowI look for new ones')
    fireEvent.click(screen.getByRole('radio', { name: '3' }))
    expect(model.getValue('Q')).toEqual({ 1: 3 })
  })

  it('keeps the pad when the flipped half sees the scale reversed', () => {
    const { question } = survey({
      type: 'matrix', rows: [{ value: 1, text: 'I look for new ones  ⟷  I stick with brands I know' }],
      columns: [...ZERO_TEN].reverse(),
    })
    render(<MatrixCarousel question={question as QuestionMatrixModel} />)
    const pad = screen.getAllByRole('radio').map((b) => b.textContent)
    expect(pad.slice(0, 3)).toEqual(['10', '9', '8'])
  })

  it('is the same pad for one thing rated', () => {
    const { model, question } = survey({
      type: 'rating', rateValues: ZERO_TEN.map((c) => ({ value: c.value, text: String(c.value) })),
      minRateDescription: 'Very Unfavorable', maxRateDescription: 'Very Favorable',
    })
    render(<RatingPad question={question as QuestionRatingModel} />)
    expect(screen.getByText('Very Favorable')).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: '8' }))
    expect(model.getValue('Q')).toBe(8)
  })
})

describe('respondentTitle', () => {
  it('never shows a respondent the file name, which names the sponsor', () => {
    expect(respondentTitle('02QRE_FINAL_-_Bubble_Consumer_Research_5.6.26')).toBe('')
    expect(respondentTitle('study.docx')).toBe('')
    expect(respondentTitle('Skincare Habits Study')).toBe('Skincare Habits Study')
  })
})

describe('ConstantSum', () => {
  it('keeps a running total and says when it adds up', () => {
    const { model, question } = survey({
      type: 'multipletext',
      items: [{ name: '1', title: 'Brands I trust', inputType: 'number' },
              { name: '2', title: 'New brands', inputType: 'number' }],
      validators: [{ type: 'expression', expression: '{Q.1} + {Q.2} = 100' }],
    })
    render(<ConstantSum question={question as Question} />)
    fireEvent.change(screen.getByLabelText('Brands I trust'), { target: { value: '70' } })
    expect(screen.getByText('70 of 100')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('New brands'), { target: { value: '40' } })
    expect(screen.getByText('110 — that is 10 over 100')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('New brands'), { target: { value: '30' } })
    expect(screen.getByText('Adds up to 100 ✓')).toBeTruthy()
    expect(model.getValue('Q')).toEqual({ 1: 70, 2: 30 })
  })
})
