import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  questionnaireApi: {
    quote: vi.fn().mockResolvedValue({
      data: {
        length: { minutes: 22, low: 15.4, high: 28.6, stated_minutes: 12,
                  longest: [{ qid: 'MAXDIFF_LIKELY', seconds: 84 }] },
        screeners: [{ qid: 'RESP_AGE', text: 'What is your age?', screens_out: 'TERMINATE if age<25' }],
        cost: null, completes: 750, n_questions: 79,
      },
    }),
  },
}))

import QuotePanel from './QuotePanel'

describe('QuotePanel', () => {
  it('shows the length range, warns when the document under-promises, lists screeners', async () => {
    render(<QuotePanel studyId="s1" />)
    expect(await screen.findByText('15.4–28.6 min')).toBeTruthy()
    expect(screen.getByText(/likely under-priced/)).toBeTruthy()
    expect(screen.getByText('RESP_AGE')).toBeTruthy()
    expect(screen.getByText(/Enter completes and incidence/)).toBeTruthy()
  })
})
