import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { table, sentences, edit } = vi.hoisted(() => {
const table = {
  id: 't1', stub: 'Q1', title: 'Heard of us', filter: '', means: null, mean_sig: null,
  columns: [{ label: 'Total', letter: null, base: 80, unweighted_base: 80, effective_base: 80 }],
  rows: [{ label: 'Yes', pct: [70], sig: [''], kind: 'category' }, { label: 'No', pct: [30], sig: [''], kind: 'category' }],
}
const sentences = [
  { id: 's1', text: 'Yes leads at 70%.', cites: [{ table: 't1', row: 0, col: 0 }], ok: true, problems: [], source: 'finding' },
  { id: 's2', text: 'Nine in ten know us.', cites: [], ok: false, source: 'person',
    problems: ['9 matches none of the cited cells. Cite the cell it comes from or change the number.'] },
]
const edit = vi.fn().mockResolvedValue({ data: {} })
return { table, sentences, edit }
})

vi.mock('../api/client', () => ({
  studiesApi: { crosstabs: vi.fn().mockResolvedValue({ data: { tables: [table] } }) },
  reportApi: {
    get: vi.fn().mockResolvedValue({ data: { sentences, drafted_at: 'x', job: null } }),
    draft: vi.fn().mockResolvedValue({ data: {} }),
    edit,
    add: vi.fn().mockResolvedValue({ data: {} }),
    remove: vi.fn().mockResolvedValue({ data: {} }),
  },
}))

import ReportPanel from './ReportPanel'

describe('ReportPanel', () => {
  beforeEach(() => edit.mockClear())

  it('shows a citation chip with the cell behind it', async () => {
    render(<ReportPanel studyId="s1" />)
    const chip = await screen.findByRole('button', { name: /Citation 1/ })
    expect(chip.textContent).toContain('T1·r1c1')
    expect(chip.getAttribute('aria-label')).toBe('Citation 1: Yes, Total: 70% (95% interval 59%–79%) · base 80')
  })

  it('strikes through a refused sentence and says what to change', async () => {
    render(<ReportPanel studyId="s1" />)
    const refused = await screen.findByText('Nine in ten know us.')
    expect(refused.className).toContain('line-through')
    expect(screen.getByText(/matches none of the cited cells/)).toBeTruthy()
    expect(screen.getByText('refused')).toBeTruthy()
  })

  it('edits a sentence in place and saves it with its cells', async () => {
    render(<ReportPanel studyId="s1" />)
    fireEvent.click(await screen.findByRole('button', { name: /Edit: Yes leads/ }))
    fireEvent.change(screen.getByLabelText('Sentence'), { target: { value: 'Yes is at 70%.' } })
    fireEvent.click(screen.getByText('Save sentence'))
    await waitFor(() => expect(edit).toHaveBeenCalledWith('s1', 's1', 'Yes is at 70%.', [{ table: 't1', row: 0, col: 0 }]))
  })
})

describe('wilson', () => {
  it('matches the server and the published reference: 50% of 100 is 40.4–59.6', async () => {
    const { wilson } = await import('./ReportPanel')
    const [lo, hi] = wilson(50, 100)!
    expect(lo).toBeCloseTo(40.38, 1)
    expect(hi).toBeCloseTo(59.62, 1)
    expect(wilson(30, 0.5)).toBeNull()
  })
})
