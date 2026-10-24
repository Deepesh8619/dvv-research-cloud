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
