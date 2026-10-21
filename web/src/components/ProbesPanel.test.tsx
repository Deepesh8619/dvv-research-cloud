import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  questionnaireApi: {
    probes: vi.fn().mockResolvedValue({ data: { questions: [
      { qid: 'DA_NON_BARRIER_OE', text: 'Why have you not tried it?', probed: false },
      { qid: 'SS_TITLE', text: 'Your job title', probed: false }] } }),
    setProbes: vi.fn().mockResolvedValue({ data: { questions: [
      { qid: 'DA_NON_BARRIER_OE', text: 'Why have you not tried it?', probed: true },
      { qid: 'SS_TITLE', text: 'Your job title', probed: false }] } }),
  },
}))

import { questionnaireApi } from '../api/client'
import ProbesPanel from './ProbesPanel'

describe('ProbesPanel', () => {
  it('saves exactly the ticked questions', async () => {
    render(<ProbesPanel studyId="s1" />)
    fireEvent.click(await screen.findByLabelText(/Why have you not tried it/))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(questionnaireApi.setProbes).toHaveBeenCalledWith('s1', ['DA_NON_BARRIER_OE'])
    expect(await screen.findByText(/Links opened from now on/)).toBeTruthy()
  })
})
