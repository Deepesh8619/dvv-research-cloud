import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const row = vi.hoisted(() => ({
  link_id: 'lA', vendor_name: 'Alpha Panel', cpi: 2, target_completes: 10, starts: 4, completes: 3,
  accepted: 2, billable: 4, incidence_pct: 75, median_loi: 600, pct_of_target: 30, pct_removed: 33.3,
  speeder_rate: 0, straightliner_rate: 0, duplicate_rate: 0,
}))

vi.mock('../api/client', () => ({
  fieldingApi: {
    suppliers: vi.fn().mockResolvedValue({ data: { vendors: [row] } }),
    setVendor: vi.fn().mockResolvedValue({ data: {} }),
  },
  downloadFile: vi.fn().mockResolvedValue(undefined),
}))

import { downloadFile, fieldingApi } from '../api/client'
import SuppliersPanel from './SuppliersPanel'

describe('SuppliersPanel', () => {
  it('shows accepted and billable, downloads the reconciliation, edits the price', async () => {
    render(<SuppliersPanel studyId="s1" canWrite />)
    expect(await screen.findByText('Alpha Panel')).toBeTruthy()
    expect(screen.getByText('2 per complete')).toBeTruthy()
    expect(screen.getByText('33.3%').className).toContain('amber')
    fireEvent.click(screen.getByRole('button', { name: 'Reconciliation' }))
    expect(downloadFile).toHaveBeenCalledWith('/studies/s1/fielding/links/lA/reconciliation.csv', 'reconciliation.csv')
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('Cost per complete'), { target: { value: '2.5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(fieldingApi.setVendor).toHaveBeenCalledWith('s1', 'lA', {
      vendor_name: 'Alpha Panel', cpi: 2.5, target_completes: 10 })
  })
})

describe('SuppliersPanel with a broken link', () => {
  it('shows the broken link as a message and keeps the others', async () => {
    vi.mocked(fieldingApi.suppliers).mockResolvedValueOnce({ data: { vendors: [row,
      { link_id: 'lB', vendor_name: 'Beta', cpi: null, target_completes: null,
        error: "This link's report could not be built; its interviews are safe." }] } } as never)
    render(<SuppliersPanel studyId="s1" canWrite />)
    expect(await screen.findByText('Alpha Panel')).toBeTruthy()
    expect(screen.getByText(/could not be built; its interviews are safe/)).toBeTruthy()
  })
})
