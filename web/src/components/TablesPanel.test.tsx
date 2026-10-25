import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  studiesApi: {
    crosstabs: vi.fn().mockResolvedValue({ data: { title: 'T', weight: null, confidence: 95, min_base_for_test: 30, tables: [{
      id: 't1', stub: 'Q3', title: 'Why', filter: '', means: null, mean_sig: null,
      columns: [{ label: 'Total', letter: null, base: 80, unweighted_base: 80, effective_base: 80 }],
      rows: [{ label: 'Price', pct: [64], sig: [''], kind: 'category' }],
    }] } }),
    norms: vi.fn().mockResolvedValue({ data: { rows: [], matched: [] } }),
    cellProvenance: vi.fn().mockResolvedValue({ data: { value: 64, label: 'Price', column: 'Total',
      steps: [{ title: 'The cell', detail: 'x' }] } }),
  },
}))

import TablesPanel from './TablesPanel'

describe('TablesPanel', () => {
  it('opens the thread from a cell and highlights it', async () => {
    render(<TablesPanel studyId="s1" />)
    fireEvent.click(await screen.findByRole('button', { name: /Where does 64.0%/ }))
    expect(await screen.findByRole('dialog', { name: 'Study thread' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Where does 64.0%/ }).closest('td')?.className).toContain('ring-amber-500')
  })
})
