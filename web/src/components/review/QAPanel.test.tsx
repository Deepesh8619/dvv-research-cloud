import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import QAPanel from './QAPanel'

describe('QAPanel', () => {
  it('splits what the agents fixed from what needs a person, and links each question', () => {
    const onGoTo = vi.fn()
    render(
      <QAPanel
        canRun
        onRun={async () => {}}
        onGoTo={onGoTo}
        qa={{
          ran_at: '2026-10-10T10:00:00+00:00',
          checked: 21,
          fixed: 1,
          needs_you: 1,
          entries: [
            { qid: 'Q10', agent: 'programmer', status: 'fixed', detail: "re-read it from the document with today's parser" },
            { qid: 'S7', agent: 'qa', status: 'needs_you', check: 'routing_not_applied',
              detail: 'the document says who is asked this, and the survey asks everyone',
              evidence: '[ASK IF MULTIPLE AT S6 SELECTED]' },
          ],
        }}
      />,
    )
    expect(screen.getByText('Needs you')).toBeTruthy()
    expect(screen.getByText('Fixed and re-checked')).toBeTruthy()
    expect(screen.getByText('[ASK IF MULTIPLE AT S6 SELECTED]')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'S7' }))
    expect(onGoTo).toHaveBeenCalledWith('S7')
    expect(screen.getByRole('button', { name: 'Run QA again' })).toBeTruthy()
  })
})

describe('QAPanel second opinion', () => {
  it('shows the AI reviewer as an opinion to judge, with its evidence and questions', () => {
    const onGoTo = vi.fn()
    render(
      <QAPanel
        canRun
        onRun={async () => {}}
        onGoTo={onGoTo}
        qa={{ ran_at: 'x', checked: 3, fixed: 0, needs_you: 0, entries: [] }}
        logicReview={{
          model: 'bedrock/qwen',
          findings: [{ qids: ['Q10', 'S1'], severity: 'error', problem: 'Q10 is only for people S1 screens out.',
            evidence: 'asked if: S1 eq 2 "No"', fix: 'Ask Q10 of S1 = 1' }],
        }}
        onRunLogicReview={async () => {}}
      />,
    )
    expect(screen.getByText(/it can be wrong, judge each/)).toBeTruthy()
    expect(screen.getByText('asked if: S1 eq 2 "No"')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Q10' }))
    expect(onGoTo).toHaveBeenCalledWith('Q10')
  })
})
