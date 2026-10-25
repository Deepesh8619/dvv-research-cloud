import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const verify = vi.fn()
const checkFile = vi.fn()
vi.mock('../api/client', () => ({
  ledgerApi: { verify: (...a: unknown[]) => verify(...a), checkFile: (...a: unknown[]) => checkFile(...a) },
  errorMessage: () => null,
}))

import LedgerStrip from './LedgerStrip'

const HEAD = 'a1b2c3d4e5f6'.padEnd(64, '0')

describe('LedgerStrip', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows an intact chain with its fingerprint', async () => {
    verify.mockResolvedValue({ data: { ok: true, n: 214, head: HEAD, first_bad_seq: null } })
    render(<LedgerStrip studyId="s1" />)
    expect(await screen.findByText(/214 entries/)).toBeTruthy()
    expect(screen.getByText(/fingerprint a1b2c3…/)).toBeTruthy()
  })

  it('names the entry where the chain breaks', async () => {
    verify.mockResolvedValue({ data: { ok: false, n: 60, head: HEAD, first_bad_seq: 57 } })
    render(<LedgerStrip studyId="s1" />)
    expect(await screen.findByText('Ledger broken at entry 57')).toBeTruthy()
  })

  it('says whether a file was issued', async () => {
    verify.mockResolvedValue({ data: { ok: true, n: 1, head: HEAD, first_bad_seq: null } })
    checkFile.mockResolvedValueOnce({ data: { issued: true, at: '2026-10-25T10:00:00+00:00', name: 'a.pptx' } })
    render(<LedgerStrip studyId="s1" />)
    const input = screen.getByLabelText('Check a file you received')
    fireEvent.change(input, { target: { files: [new File(['x'], 'a.pptx')] } })
    expect(await screen.findByText('This file was issued on 25 Oct 2026')).toBeTruthy()

    checkFile.mockResolvedValueOnce({ data: { issued: false, at: null, name: null } })
    fireEvent.change(input, { target: { files: [new File(['y'], 'b.pptx')] } })
    await waitFor(() =>
      expect(screen.getByText('This file was not issued by this study')).toBeTruthy(),
    )
  })
})
