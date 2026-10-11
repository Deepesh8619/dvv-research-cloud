import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

vi.mock('../../api/client', () => ({
  questionnaireApi: {
    preview: vi.fn().mockResolvedValue({
      data: {
        survey_json: {
          showNavigationButtons: false,
          pages: [{ name: 'preview', elements: [{ type: 'radiogroup', name: 'Q3', title: 'Are you familiar with TRD?', choices: ['Yes', 'No'] }] }],
        },
        notes: ['In the link this question only shows when its condition is met.'],
        problems: ['the respondent sees this question with nothing to answer'],
        theme: null,
      },
    }),
  },
}))

import QuestionPreview from './QuestionPreview'
import { questionnaireApi } from '../../api/client'

describe('QuestionPreview', () => {
  it('renders the question as the link would, with what QA and the link would say', async () => {
    render(<QuestionPreview studyId="s" qid="Q3" current={() => ({ text: 'x' })} version="1" />)
    await waitFor(() => screen.getByText('Are you familiar with TRD?'))
    expect(questionnaireApi.preview).toHaveBeenCalledWith('s', 'Q3', { text: 'x' })
    expect(screen.getByText('Yes')).toBeTruthy()
    expect(screen.getByText(/only shows when its condition is met/)).toBeTruthy()
    expect(screen.getByText(/QA would flag this/)).toBeTruthy()
  })
})
