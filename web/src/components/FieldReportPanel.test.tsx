import { fireEvent, render, screen } from '@testing-library/react'
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

  it('shows each question as a card, and the same numbers as a table', async () => {
    await renderWith({
      ...base,
      questions: [
        {
          qid: 'Q2a', text: 'Which brands have you heard of?', type: 'multi', base: 2, answered: 2,
          options: [{ code: 1, label: 'Bubble', n: 1 }, { code: 2, label: 'Anua', n: 2 }],
        },
      ],
    })
    expect(await screen.findByText('2 Responses')).toBeTruthy()
    expect(screen.getByText('Which brands have you heard of?')).toBeTruthy()
    expect(screen.getByText('50%')).toBeTruthy() // 1 of the 2 completes
    fireEvent.click(screen.getByRole('tab', { name: 'tabular' }))
    expect(screen.getByRole('table')).toBeTruthy()
    expect(screen.getByText('100%')).toBeTruthy()
  })

  it('groups cards into buckets and follows a link across them', async () => {
    const links = (over: Record<string, unknown>) => ({
      asked_if: [], asked_if_text: null, screens_out_on: [], options_from: [], text_from: [],
      computed_from: [], loop_of: null, used_by: [], ...over,
    })
    const card = (qid: string, bucket: string, over: Record<string, unknown> = {}) => ({
      qid, text: `${qid} text`, type: 'multi', base: 2, answered: 2, bucket,
      bucket_reason: '', bucket_suggested: false, options: [], links: links(over),
    })
    await renderWith({
      ...base,
      questions: [
        card('S1', 'screener', { used_by: ['Q2b'] }),
        card('Q2a', 'core', { used_by: ['Q2b'] }),
        card('Q2b', 'core', { options_from: ['Q2a'], asked_if: ['S1'] }),
      ],
    })
    expect(await screen.findByRole('tab', { name: /Screener 1/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: /Core measures 2/ }))
    expect(screen.queryByText('S1 text')).toBeNull() // filtered to core
    Element.prototype.scrollIntoView = vi.fn()
    // Q2b is asked if S1 — a screener, outside the filter. Following it shows all.
    fireEvent.click(screen.getByRole('button', { name: 'S1' }))
    expect(await screen.findByText('S1 text')).toBeTruthy()
  })
})
