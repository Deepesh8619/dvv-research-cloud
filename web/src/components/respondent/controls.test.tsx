import { act, fireEvent, render, screen } from '@testing-library/react'
import { Model } from 'survey-core'
import type { Question, QuestionMatrixModel, QuestionRankingModel } from 'survey-core'
import { describe, expect, it, vi } from 'vitest'

import { MatrixCarousel, NumberStepper, TapToRank } from './controls'

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
