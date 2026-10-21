import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  studiesApi: {
    findings: vi.fn().mockResolvedValue({ data: { n_tables: 3, findings: [
      { kind: 'difference', text: 'Brand — Alpha: 52% among Under 35 against 25% among 35+, a significant difference.',
        tables: ['t5'], caution: 'Base under 30 for 35+: indicative only.',
        cites: [{ table: 't5', row: 'Alpha', column: 'Under 35', value: 52, base: 100 },
                { table: 't5', row: 'Alpha', column: '35+', value: 25, base: 24 }] },
      { kind: 'sample', text: 'Work status: Full-time leads at 93%, ahead of Self-employed at 7%.',
        tables: ['t2'], caution: null,
        cites: [{ table: 't2', row: 'Full-time', column: 'Total', value: 93, base: 200 }] },
    ] } }),
  },
}))

import FindingsPanel from './FindingsPanel'

describe('FindingsPanel', () => {
  it('shows tested differences with their cells and keeps the sample apart', async () => {
    render(<FindingsPanel studyId="s1" />)
    expect(await screen.findByText(/52% among Under 35/)).toBeTruthy()
    expect(screen.getByText('significant')).toBeTruthy()
    expect(screen.getByText('T5 · 35+ 25% · n=24')).toBeTruthy()
    expect(screen.getByText(/indicative only/)).toBeTruthy()
    expect(screen.getByText('Who was interviewed (screening questions)')).toBeTruthy()
  })
})
