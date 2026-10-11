import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../api/client', () => ({
  questionnaireApi: {
    questionAgent: vi.fn().mockResolvedValue({
      data: {
        error: null,
        suggestions: [
          {
            source: 'programmer agent',
            explanation: 'read it as 8 number boxes that must add up to 100',
            question: { qid: 'Q2', text: 'What proportion?', type: 'numeric', options: [], rows: [{ code: 1, label: 'Talk therapy alone' }], validation: { sum: 100 }, flags: [] },
            change: { qid: 'Q2', verdict: 'improved', only_in: null, flags_cleared: [], flags_added: [],
              changes: [{ field: 'type', verdict: 'changed', before: 'single', after: 'numeric' }] },
            qa_before: ['the document says these 8 answers must add up to 100; the survey asks it as single'],
            qa_after: [],
            dropped: [],
          },
        ],
      },
    }),
  },
}))

import QuestionAgent from './QuestionAgent'
import { questionnaireApi } from '../../api/client'

describe('QuestionAgent', () => {
  it('sends what is on screen with the words, shows QA before→after, and hands the fix to the editor', async () => {
    const onUse = vi.fn()
    const onTeach = vi.fn()
    render(<QuestionAgent studyId="s" qid="Q2" current={() => ({ type: 'single' })} onUse={onUse} onTeach={onTeach} />)
    fireEvent.change(screen.getByLabelText('what is wrong with this question'), {
      target: { value: 'these must add up to 100' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Ask the agent' }))
    await waitFor(() => screen.getByText('read it as 8 number boxes that must add up to 100'))
    expect(questionnaireApi.questionAgent).toHaveBeenCalledWith('s', 'Q2', {
      instruction: 'these must add up to 100',
      current: { type: 'single' },
    })
    expect(screen.getByText(/1 problem →/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Use this' }))
    expect(onUse).toHaveBeenCalledWith(expect.objectContaining({ type: 'numeric' }))
    fireEvent.click(screen.getByRole('button', { name: 'Teach the parser this' }))
    expect(onTeach).toHaveBeenCalledWith('these must add up to 100')
  })
})
