import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../lib/useStudyEvents', () => ({ useStudyEvents: vi.fn().mockReturnValue(true) }))
vi.mock('../api/client', () => ({
  fieldingApi: {
    suppliers: vi.fn().mockResolvedValue({ data: { vendors: [
      { link_id: 'l1', vendor_name: 'Alpha Panel', target_completes: 100, completes: 40, billable: 800, pct_removed: 5 },
      { link_id: 'l2', vendor_name: 'Beta Panel', target_completes: 100, completes: 20, billable: 300, pct_removed: 31.4 },
    ] } }),
    listQuotas: vi.fn().mockResolvedValue({ data: { cells: [
      { id: 'q1', name: 'Women', limit: 50, count: 40 }, { id: 'q2', name: 'North', limit: 10, count: 10 },
    ] } }),
    closeFieldwork: vi.fn().mockResolvedValue({ data: { run_id: 'r1' } }),
    workflowRun: vi.fn().mockResolvedValue({ data: { id: 'r1', state: 'done', steps: [] } }),
    fieldReport: vi.fn().mockResolvedValue({ data: { starts: 80, median_loi_sec: 840, incidence_pct: 38 } }),
  },
}))

import ControlRoom from './ControlRoom'

describe('ControlRoom', () => {
  it('shows gauges, totals and supplier rows', async () => {
    render(<ControlRoom studyId="s1" links={[{ id: 'l1', is_test: false } as never]} />)
    expect(await screen.findByText('Alpha Panel')).toBeTruthy()
    expect(screen.getByText('Beta Panel')).toBeTruthy()
    expect(screen.getByText('60 / 200 completes')).toBeTruthy()
    expect(screen.getByText('LOI 14 min')).toBeTruthy()
    expect(screen.getByText('IR 38%')).toBeTruthy()
    expect(screen.getByRole('meter', { name: 'Women quota' }).getAttribute('aria-valuenow')).toBe('40')
    expect(screen.getByText('10 / 10 · full')).toBeTruthy()
    expect(screen.getByText('31% (high)')).toBeTruthy()
  })

  it('asks before closing fieldwork', async () => {
    const { fieldingApi } = await import('../api/client')
    render(<ControlRoom studyId="s1" links={[{ id: 'l1', is_test: false } as never]} />)
    fireEvent.click(await screen.findByText('Close fieldwork and build deliverables'))
    expect(fieldingApi.closeFieldwork).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Yes, build deliverables'))
    expect(await screen.findByText(/Deliverables are ready/)).toBeTruthy()
    expect(fieldingApi.closeFieldwork).toHaveBeenCalledWith('s1')
  })
})
