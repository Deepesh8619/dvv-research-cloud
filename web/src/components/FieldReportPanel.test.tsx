import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import FieldReportPanel from './FieldReportPanel'

/**
 * The two distinctions a field report must not blur.
 *
 * "We don't know yet" and "nobody qualifies" are different answers to a
 * client's question, and rendering the first as 0% turns a study that has not
 * started into one that has failed.
 */
const base = {
  starts: 5,
  dispositions: { complete: 2, terminate: 3, security: 0, overquota: 0, in_progress: 0 },
  incidence_pct: 40,
  terminates_by_question: [{ qid: 'S1', n: 3 }],
  overquota_by_cell: [],
  median_loi_sec: 372,
  dropoff_by_question: [],
}

vi.mock('../api/client', () => ({
  fieldingApi: { fieldReport: vi.fn() },
}))

async function renderWith(report: Record<string, unknown>) {
  const { fieldingApi } = await import('../api/client')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(fieldingApi.fieldReport as any).mockResolvedValue({ data: report })
  render(<FieldReportPanel studyId="s1" linkId="l1" />)
  return screen.findByText('incidence')
}

describe('FieldReportPanel', () => {
  it('shows incidence against the number of people actually judged', async () => {
    await renderWith(base)
    expect(await screen.findByText('40%')).toBeTruthy()
    expect(screen.getByText('2 of 5 judged')).toBeTruthy()
  })

  it('says "not yet" rather than 0% before anyone has been judged', async () => {
    await renderWith({
      ...base,
      dispositions: { ...base.dispositions, complete: 0, terminate: 0, in_progress: 5 },
      incidence_pct: null,
      median_loi_sec: null,
    })
    expect(await screen.findByText('not yet')).toBeTruthy()
    expect(screen.getByText('nobody judged yet')).toBeTruthy()
    expect(screen.queryByText('0%')).toBeNull()
  })

  it('reads the interview length in minutes, not raw seconds', async () => {
    await renderWith(base)
    expect(await screen.findByText('6m 12s')).toBeTruthy()
  })

  it('names the question that screened people out', async () => {
    await renderWith(base)
    expect(await screen.findByText('S1')).toBeTruthy()
    expect(screen.getByText('No cell has turned anyone away.')).toBeTruthy()
  })
})
