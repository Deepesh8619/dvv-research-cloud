import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  questionnaireApi: {
    quotaPlan: vi.fn().mockResolvedValue({ data: { completes: null, cells: [], limits: [] } }),
    saveQuotaPlan: vi.fn().mockResolvedValue({ data: { completes: 400, cells: [], limits: [{ name: 'Women', limit: 200 }] } }),
  },
}))

import { questionnaireApi } from '../api/client'
import QuotaPlanPanel from './QuotaPlanPanel'

const gender = { qid: 'S_GENDER', text: 'How do you describe yourself?', type: 'single',
  options: [{ code: 1, label: 'Man' }, { code: 2, label: 'Woman' }] } as never

describe('QuotaPlanPanel', () => {
  it('builds a quota from a question, its answer and a share', async () => {
    render(<QuotaPlanPanel studyId="s1" questions={[gender]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Quotas' }))
    fireEvent.change(screen.getByLabelText('Completes needed'), { target: { value: '400' } })
    fireEvent.click(screen.getByRole('button', { name: '+ Add quota' }))
    fireEvent.change(screen.getByLabelText('quota name'), { target: { value: 'Women' } })
    fireEvent.change(screen.getByLabelText('quota question'), { target: { value: 'S_GENDER' } })
    fireEvent.click(screen.getByRole('button', { name: 'Woman' }))
    fireEvent.change(screen.getByLabelText('quota share'), { target: { value: '50' } })
    expect(screen.getByText('= 200')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Save quotas' }))
    expect(questionnaireApi.saveQuotaPlan).toHaveBeenCalledWith('s1', { completes: 400,
      cells: [{ name: 'Women', qid: 'S_GENDER', codes: [2], share_pct: 50 }] })
    expect(await screen.findByText('Saved: Women 200.')).toBeTruthy()
  })
})

describe('QuotaPlanPanel bands', () => {
  it('asks a number question for a band instead of answers', async () => {
    const age = { qid: 'S_AGE', text: 'What is your age?', type: 'numeric', options: [] } as never
    render(<QuotaPlanPanel studyId="s1" questions={[age]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Quotas' }))
    fireEvent.change(screen.getByLabelText('Completes needed'), { target: { value: '100' } })
    fireEvent.click(screen.getByRole('button', { name: '+ Add quota' }))
    fireEvent.change(screen.getByLabelText('quota question'), { target: { value: 'S_AGE' } })
    fireEvent.change(screen.getByLabelText('band from'), { target: { value: '18' } })
    fireEvent.change(screen.getByLabelText('band to'), { target: { value: '34' } })
    fireEvent.change(screen.getByLabelText('quota share'), { target: { value: '40' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save quotas' }))
    expect(vi.mocked(questionnaireApi.saveQuotaPlan).mock.calls.at(-1)?.[1].cells[0]).toMatchObject(
      { qid: 'S_AGE', min: 18, max: 34, share_pct: 40 })
  })
})
