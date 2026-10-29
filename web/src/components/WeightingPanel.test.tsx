import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import WeightingPanel from './WeightingPanel'

const report = {
  converged: true, iterations: 3, efficiency_pct: 92.1, effective_n: 36.8, n: 40, min_weight: 0.8,
  max_weight: 1.2, trimmed: 0,
  targets: { GEND: { '1': { label: 'Male', target_pct: 40, weighted_pct: 40, unweighted_pct: 50 },
    '2': { label: 'Female', target_pct: 60, weighted_pct: 60, unweighted_pct: 50 } } },
}

vi.mock('../api/client', () => ({
  studiesApi: {
    weighting: vi.fn().mockResolvedValue({ data: { applied: false, weight_variable: null, candidates: [
      { name: 'GEND', label: 'Gender', codes: [{ code: 1, label: 'Male' }, { code: 2, label: 'Female' }] }] } }),
    previewWeighting: vi.fn(async () => ({ data: report })),
    applyWeighting: vi.fn(async () => ({ data: {} })),
    removeWeighting: vi.fn(),
  },
  errorMessage: () => null,
}))

describe('WeightingPanel', () => {
  it('previews before it applies, with the efficiency and achieved shares', async () => {
    const { studiesApi } = await import('../api/client')
    render(<WeightingPanel studyId="s1" />)
    fireEvent.change(await screen.findByLabelText('Add a weighting variable'), { target: { value: 'GEND' } })
    fireEvent.change(screen.getByLabelText('Male'), { target: { value: '40' } })
    fireEvent.change(screen.getByLabelText('Female'), { target: { value: '60' } })
    const apply = screen.getByRole('button', { name: 'Apply weighting' }) as HTMLButtonElement
    expect(apply.disabled).toBe(true) // a preview comes first
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))
    expect(await screen.findByText(/92.1%/)).toBeTruthy()
    expect(studiesApi.previewWeighting).toHaveBeenCalledWith('s1', { GEND: { '1': 40, '2': 60 } })
    fireEvent.click(apply)
    await waitFor(() => expect(studiesApi.applyWeighting).toHaveBeenCalledWith('s1', { GEND: { '1': 40, '2': 60 } }))
  })
})
