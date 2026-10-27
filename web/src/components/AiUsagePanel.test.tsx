import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import AiUsagePanel from './AiUsagePanel'

vi.mock('../api/client', () => ({
  usageApi: {
    get: vi.fn().mockResolvedValue({
      data: {
        days: [{ day: '2026-10-27', copilot_questions: 140, calls: 412, input_tokens: 2_100_000,
                 output_tokens: 61_000, estimated_usd: 0.35, copilot_usd: 0.31, unpriced_calls: 0 }],
        month: { month: '2026-10', copilot_questions: 140, calls: 412, estimated_usd: 0.35 },
        prices_per_million: { bedrock: { input: 0.15, output: 0.6 }, local: { input: 0, output: 0 } },
        note: 'Estimated from the token counts the models report.',
      },
    }),
  },
  errorMessage: () => null,
}))

describe('AiUsagePanel', () => {
  it('shows the month and each day, copilot apart from the rest', async () => {
    render(<AiUsagePanel />)
    expect(await screen.findAllByText('$0.35')).toHaveLength(2)
    expect(screen.getByText('$0.31')).toBeTruthy()
    expect(screen.getByText('2.1M / 61.0k')).toBeTruthy()
    expect(screen.getByText(/\$0.15 in \/ \$0.6 out per million/)).toBeTruthy()
  })
})
