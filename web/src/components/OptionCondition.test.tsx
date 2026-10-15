import { fireEvent, render, screen } from '@testing-library/react'
import { describe as suite, expect, it, vi } from 'vitest'

import OptionCondition, { describe } from './OptionCondition'

suite('OptionCondition', () => {
  it('shows an answer condition read from the document as a badge', () => {
    render(
      <OptionCondition
        condition={{ op: 'in', qid: 'S11', values: [1], verbatim_text: 'ONLY SHOW IF S11=1' }}
        onChange={() => {}}
        qidOptions={['S11']}
        disabled={false}
      />,
    )
    expect(screen.getByText('Only shown if: ONLY SHOW IF S11=1')).toBeTruthy()
  })

  it('lets a person add a condition to an answer', () => {
    const onChange = vi.fn()
    render(<OptionCondition condition={null} onChange={onChange} qidOptions={['Q2']} disabled={false} />)
    fireEvent.click(screen.getByText('+ only show this answer if…'))
    fireEvent.click(screen.getByRole('button', { name: /add condition/i }))
    // A blank line is not yet a condition, but it stays on screen to be filled in.
    expect(screen.getAllByRole('combobox').length).toBeGreaterThan(0)
  })

  it('describes a built condition in words', () => {
    expect(describe({ op: 'selected', qid: 'Q2', value: 3 })).toBe('Q2 selected 3')
  })
})
