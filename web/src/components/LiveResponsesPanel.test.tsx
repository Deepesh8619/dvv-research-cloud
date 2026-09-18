import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import LiveResponsesPanel from './LiveResponsesPanel'

vi.mock('../api/client', () => ({ fieldingApi: { liveResponses: vi.fn() } }))

async function mocked() {
  const { fieldingApi } = await import('../api/client')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return fieldingApi.liveResponses as any
}

describe('LiveResponsesPanel', () => {
  it('shows each respondent with their status and answers as labels', async () => {
    ;(await mocked()).mockResolvedValue({
      data: {
        columns: ['S1', 'Q2'],
        total: 1,
        rows: [{ id: 'ab12cd34', status: 'complete', started_at: null, last_seen_at: null,
                 answers: { S1: 'Female', Q2: 'Alpha, Beta' } }],
      },
    })
    render(<LiveResponsesPanel studyId="s" linkId="l" />)
    expect(await screen.findByText('Alpha, Beta')).toBeTruthy()
    expect(screen.getByText('complete')).toBeTruthy()
    expect(screen.getByText('1 respondent')).toBeTruthy()
  })

  it('says when the data opens instead of failing', async () => {
    ;(await mocked()).mockRejectedValue({
      response: { status: 403, data: { detail: 'Available once the soft launch reaches 50 completes (currently 3).' } },
    })
    render(<LiveResponsesPanel studyId="s" linkId="l" />)
    expect(await screen.findByText(/reaches 50 completes \(currently 3\)/)).toBeTruthy()
  })
})
