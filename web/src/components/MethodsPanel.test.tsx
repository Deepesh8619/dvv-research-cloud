import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  studiesApi: {
    methodInputs: vi.fn().mockResolvedValue({ data: {
      n_respondents: 62, maxdiff: [{ exercise: 'MAXDIFF_LIKELY_MD', screens: 12 }],
      multi_select: [{ question: 'SS_CHANNELS', text: 'Channels' }], numeric: [], ratings: [] } }),
    runMethod: vi.fn().mockResolvedValue({ data: {
      n_respondents: 65, basis: 'per time shown', model: 'aggregate best-worst logit', items: [
        { item: 12, label: 'ai_audience_grow', best: 82, worst: 22, score: 0.275, utility: 0.61, share: 71.2 },
        { item: 2, label: 'cash_back', best: 41, worst: 83, score: -0.191, utility: -0.61, share: 28.8 }] } }),
  },
}))

import { studiesApi } from '../api/client'
import MethodsPanel from './MethodsPanel'

describe('MethodsPanel', () => {
  it('offers only the methods the data supports and shows MaxDiff scores', async () => {
    render(<MethodsPanel studyId="s1" />)
    await screen.findByText(/On the 62 completes/)
    expect((screen.getByRole('button', { name: 'Van Westendorp' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Exercise'), { target: { value: 'MAXDIFF_LIKELY_MD' } })
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(studiesApi.runMethod).toHaveBeenCalledWith('s1', { method: 'maxdiff', exercise: 'MAXDIFF_LIKELY_MD' })
    expect(await screen.findByText('ai_audience_grow')).toBeTruthy()
    expect(screen.getByText('-0.191')).toBeTruthy()
    expect(screen.getByText('71.2%')).toBeTruthy()
  })
})
