import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  studiesApi: {
    list: vi.fn().mockResolvedValue({ data: { studies: [
      { id: 'w1', client_name: 'Wave 1', excel_path: '/x' }, { id: 'w2', client_name: 'Wave 2', excel_path: '/x' }] } }),
    stackCheck: vi.fn().mockResolvedValue({ data: {
      sources: ['Wave 2', 'Wave 1'], n_variables: 40, n_in_every_source: 38, n_breaking: 1,
      not_in_every_source: [],
      changed: [{ variable: 'vlist', label: 'Sample', source: 'Wave 1', script: false, breaking: true,
                  problems: ["codes renumbered (12 answers moved): 'Sago' is 4 here, 3 in Wave 2"] }],
    } }),
    tracking: vi.fn().mockResolvedValue({ data: { skipped: {}, variables: [
      { name: 'AWARE', label: 'Aware of us', n_significant: 1,
        deltas: [{ code: 1, label: 'Yes', pct_baseline: 34, pct_current: 41, delta: 7, significant: true }] }] } }),
  },
  downloadFile: vi.fn(),
}))

import TrackerPanel from './TrackerPanel'

describe('TrackerPanel', () => {
  it('names a renumbered list, shows what moved, and refuses to stack', async () => {
    render(<TrackerPanel study={{ id: 'w2', client_name: 'Wave 2' } as never} />)
    fireEvent.click(await screen.findByLabelText('Wave 1'))
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }))
    expect(await screen.findByText(/1 question means different things/)).toBeTruthy()
    expect(screen.getByText(/codes renumbered/)).toBeTruthy()
    expect(await screen.findByText('Aware of us: Yes 34% → 41% (+7 points)')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Download stacked SPSS file' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
