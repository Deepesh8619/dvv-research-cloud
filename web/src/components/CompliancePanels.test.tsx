import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const hit = vi.hoisted(() => ({ respondent: 'r7', column: 'Q24r8oe', question: 'Other reason', terms: ['rash'],
              sentence: 'My patient developed a rash after the second dose.', answer: '' }))

vi.mock('../api/client', () => ({
  complianceApi: {
    notice: vi.fn().mockResolvedValue({ data: {
      health_study: true, complete: false, consent_questions: [],
      items: [{ item: 'consent', label: 'a question respondents agree on', present: false },
              { item: 'adverse_events', label: 'that side effects they describe will be reported', present: false }],
    } }),
    addNotice: vi.fn().mockResolvedValue({ data: {
      health_study: true, complete: true, consent_questions: ['CONSENT'],
      items: [{ item: 'consent', label: 'a question respondents agree on', present: true }],
    } }),
    adverseEvents: vi.fn().mockResolvedValue({ data: {
      n_respondents: 131, n_open_ends: 11, possible: [hit],
      mentions: [{ ...hit, respondent: 'r9', terms: ['side effects'], sentence: 'duration of side effects' }],
    } }),
  },
}))

import { complianceApi } from '../api/client'
import AdverseEventsPanel from './AdverseEventsPanel'
import NoticePanel from './NoticePanel'

describe('compliance panels', () => {
  it('lists what the notice lacks and adds it once the three fields are filled', async () => {
    render(<NoticePanel studyId="s1" />)
    expect(await screen.findByText(/this is a health study/)).toBeTruthy()
    const button = screen.getByRole('button', { name: 'Add notice and consent question' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Organisation'), { target: { value: 'DVV Research' } })
    fireEvent.change(screen.getByLabelText('Purpose of the research'), { target: { value: 'treatment choices' } })
    fireEvent.change(screen.getByLabelText('Contact for withdrawal and grievances'), { target: { value: 'p@dvv.in' } })
    fireEvent.click(button)
    expect(complianceApi.addNotice).toHaveBeenCalledWith('s1', {
      organisation: 'DVV Research', purpose: 'treatment choices', contact: 'p@dvv.in' })
    expect(await screen.findByText(/present/)).toBeTruthy()
  })

  it('puts possible events first and keeps mentions folded', async () => {
    render(<AdverseEventsPanel studyId="s1" />)
    expect(await screen.findByText(/1 possible adverse event: someone had it/)).toBeTruthy()
    expect(screen.getByText(/developed a rash/)).toBeTruthy()
    expect(screen.getByText(/1 mention with nobody said to have had it/)).toBeTruthy()
  })
})
