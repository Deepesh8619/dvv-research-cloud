import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import StudyUnderstandingPanel from './StudyUnderstandingPanel'

describe('StudyUnderstandingPanel', () => {
  it('shows each understanding with the instruction it quotes, and links its questions', () => {
    const onGoTo = vi.fn()
    render(
      <StudyUnderstandingPanel
        onGoTo={onGoTo}
        understanding={{
          summary: 'Skincare study among female consumers.',
          rules: [
            {
              instruction: 'Do not term CRM',
              meaning: 'Screen-outs apply to panel respondents only',
              applies_to: ['S2'],
              evidence: "PN: Do not term respondents from Bubble's CRM",
            },
          ],
          concerns: [{ issue: 'Screening says 10-999 but a quota is 2-9', evidence: 'Micro 2-9 Employees' }],
          claims_dropped_unsupported: 1,
          model: 'bedrock/qwen',
        }}
      />,
    )
    expect(screen.getByText('Skincare study among female consumers.')).toBeTruthy()
    expect(screen.getByText("PN: Do not term respondents from Bubble's CRM")).toBeTruthy()
    expect(screen.getByText('Raise with the client')).toBeTruthy()
    expect(screen.getByText(/1 statement\(s\) it made were dropped/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'S2' }))
    expect(onGoTo).toHaveBeenCalledWith('S2')
  })

  it('says plainly when the instructions could not be read', () => {
    render(<StudyUnderstandingPanel onGoTo={() => {}} understanding={{ unavailable: 'down' }} />)
    expect(screen.getByText(/could not be read this time/)).toBeTruthy()
  })
})
