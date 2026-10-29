import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { VariableMatch } from '../api/client'
import VariableMatchTable from './VariableMatchTable'

const match: VariableMatch = {
  summary: { rules_met: 3, rules_broken: 1, data_only: 2 },
  questions: [
    { qid: 'Q2', text: 'Why Alpha?', type: 'single', columns: ['Q2'], n_columns: 1, status: 'broken',
      rules: [{ rule: 'skip', label: 'Skip (who is asked)', result: 'broken', n_rows: 1,
        detail: '1 respondent answered with Q1 = 2', example_rows: [2] }] },
    { qid: 'Q3', text: 'Never fielded', type: 'single', columns: [], n_columns: 0,
      status: 'missing_in_data', rules: [] },
    { qid: 'Q4', text: 'Cups a day', type: 'numeric', columns: ['Q4'], n_columns: 1, status: 'met',
      rules: [{ rule: 'range', label: 'Number range', result: 'met' }] },
  ],
  data_only: [
    { column: 'zz_q9', label: 'Odd', role: 'question', role_label: 'Question', to_chase: true },
    { column: 'HID_SEG', label: 'Segment', role: 'hidden', role_label: 'Hidden variable', to_chase: false },
  ],
}

describe('VariableMatchTable', () => {
  it('shows each question with what met and what broke, and filters', () => {
    render(<VariableMatchTable match={match} />)
    expect(screen.getByText('Why Alpha?')).toBeTruthy()
    expect(screen.getByText('Not in the data', { selector: 'span' })).toBeTruthy()
    fireEvent.click(screen.getByText('Why Alpha?'))
    expect(screen.getByText(/1 respondent answered with Q1 = 2/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^All rules met \d/ }))
    expect(screen.queryByText('Why Alpha?')).toBeNull()
    expect(screen.getByText('Cups a day')).toBeTruthy()
  })
})
