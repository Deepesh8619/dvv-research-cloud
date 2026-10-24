import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  questionnaireApi: {
    expertChecks: vi.fn().mockResolvedValue({ data: {
      length: { minutes: 22.4, low: 15.7, high: 29.1 }, n_fix: 1, n_consider: 1,
      notes: [{ qid: null, weight: 'consider', note: 'About 22 minutes. Past 20, drop-out rises.' },
              { qid: 'Q4', weight: 'fix', note: 'The wording suggests an answer.' }] } }),
  },
}))

import ExpertChecks from './ExpertChecks'

describe('ExpertChecks', () => {
  it('shows length and notes, and jumps to the question a note is about', async () => {
    const onGoTo = vi.fn()
    render(<ExpertChecks studyId="s1" version="v1" onGoTo={onGoTo} />)
    fireEvent.click(await screen.findByRole('button', { name: '≈ 16–29 min · 2 notes' }, { timeout: 2000 }))
    expect(screen.getByText(/drop-out rises/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Q4' }))
    expect(onGoTo).toHaveBeenCalledWith('Q4')
  })
})
