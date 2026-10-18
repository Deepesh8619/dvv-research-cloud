import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  questionnaireApi: {
    datamap: vi.fn().mockResolvedValue({
      data: {
        n_variables: 3, status: 'approved',
        variables: [
          { name: 'D2', label: 'People in your home', type: 'numeric', values: null, question_ref: 'D2', derived: false, filled_by: 'asked', how: '' },
          { name: 'D2a', label: 'CODE SIZE OF HOUSEHOLD', type: 'single', values: { 1: '1', 4: '4 or more' }, question_ref: 'D2a', derived: false, filled_by: 'computed', how: '1 when D2 = 1; 4 when D2 ≥ 4' },
          { name: 'C7a', label: 'CODE REGION', type: 'single', values: { 1: 'West' }, question_ref: 'C7a', derived: false, filled_by: 'not_filled', how: 'no approved rule — the column will be empty' },
        ],
      },
    }),
  },
}))

import VariablesPanel from './VariablesPanel'

describe('VariablesPanel', () => {
  it('lists every variable with what fills it and warns about empty ones', async () => {
    render(<VariablesPanel studyId="s1" />)
    expect(await screen.findByText('D2a')).toBeTruthy()
    expect(screen.getByText('1 when D2 = 1; 4 when D2 ≥ 4')).toBeTruthy()
    expect(screen.getByText(/1 hidden variable will arrive empty/)).toBeTruthy()

    fireEvent.click(screen.getByRole('tab', { name: /Not filled/ }))
    expect(screen.queryByText('D2a')).toBeNull()
    expect(screen.getByText('C7a')).toBeTruthy()

    fireEvent.click(screen.getByRole('tab', { name: /All/ }))
    fireEvent.click(screen.getByText('2 codes'))
    expect(screen.getByText('4 or more')).toBeTruthy()
  })
})
