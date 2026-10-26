import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import RoutingEditor, { describeCondition, type RoutingQuestion } from './RoutingEditor'

const coffee: RoutingQuestion = { qid: 'Q2', text: 'Do you drink coffee?', type: 'single',
  options: [{ code: 1, label: 'Yes' }, { code: 2, label: 'No' }] as RoutingQuestion['options'] }
const age: RoutingQuestion = { qid: 'S_AGE', text: 'What is your age?', type: 'numeric', options: [] }

describe('conditions in plain words', () => {
  it('reads a condition back as a sentence', () => {
    expect(describeCondition({ op: 'eq', qid: 'Q2', value: 1 }, [coffee])).toBe('Do you drink coffee? is Yes')
    expect(describeCondition({ op: 'lt', qid: 'S_AGE', value: 18 }, [age])).toBe('What is your age? is less than 18')
  })

  it('picks the question by wording and the answer by its label, storing the code', () => {
    const onChange = vi.fn()
    render(<RoutingEditor label="Ask this question only if" qidOptions={['Q2']} questions={[coffee]}
      flat={{ connective: 'and', conditions: [{ op: 'eq', qid: 'Q2', value: '' }], raw: null, editable: true }}
      onChange={onChange} />)
    expect(screen.getByRole('option', { name: 'Q2 — Do you drink coffee?' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'is any of' })).toBeTruthy()
    fireEvent.change(screen.getByLabelText('condition answer'), { target: { value: '1' } })
    expect(onChange.mock.calls[0][0].conditions[0]).toMatchObject({ qid: 'Q2', op: 'eq', value: 1 })
  })

  it('picks "is any of" answers as chips', () => {
    const onChange = vi.fn()
    render(<RoutingEditor label="x" qidOptions={['Q2']} questions={[coffee]}
      flat={{ connective: 'and', conditions: [{ op: 'in', qid: 'Q2', values: [] }], raw: null, editable: true }}
      onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'No' }))
    expect(onChange.mock.calls[0][0].conditions[0].values).toEqual([2])
  })
})

describe('a condition on one row of a grid', () => {
  const dined: RoutingQuestion = {
    qid: 'S8', text: 'When did you last dine in at each?', type: 'grid_single', options: [],
    rows: [{ code: 1, label: 'Pizza' }, { code: 2, label: 'Bar & Grill' }] as RoutingQuestion['options'],
    cols: [{ code: 1, label: 'Past 30 days' }, { code: 6, label: 'Never' }] as RoutingQuestion['options'],
  }

  it('picks the row, then an answer from the grid scale, and reads it back', () => {
    const onChange = vi.fn()
    const { rerender } = render(<RoutingEditor label="Screen out if" qidOptions={['S8']} questions={[dined]}
      flat={{ connective: 'and', conditions: [{ op: 'eq', qid: 'S8', value: '' }], raw: null, editable: true }}
      onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('condition row'), { target: { value: '2' } })
    expect(onChange.mock.calls[0][0].conditions[0]).toMatchObject({ qid: 'S8', row: 2 })
    rerender(<RoutingEditor label="Screen out if" qidOptions={['S8']} questions={[dined]}
      flat={{ connective: 'and', conditions: [{ op: 'eq', qid: 'S8', row: 2, value: '' }], raw: null, editable: true }}
      onChange={onChange} />)
    expect(screen.getByRole('option', { name: 'Never' })).toBeTruthy()
    fireEvent.change(screen.getByLabelText('condition answer'), { target: { value: '6' } })
    expect(onChange.mock.calls[onChange.mock.calls.length - 1][0].conditions[0]).toMatchObject({ qid: 'S8', row: 2, op: 'eq', value: 6 })
    expect(describeCondition({ op: 'eq', qid: 'S8', row: 2, value: 6 }, [dined])).toBe('S8 — Bar & Grill is Never')
  })
})
