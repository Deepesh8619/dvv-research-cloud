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

  it('drops a question the second look settled, so the two panels agree', () => {
    render(
      <QAPanel
        canRun={false}
        onRun={async () => {}}
        onGoTo={() => {}}
        qa={{
          ran_at: '2026-10-17T19:38:51+00:00', checked: 28, fixed: 0, needs_you: 2,
          entries: [
            { qid: 'Q23', agent: 'qa', status: 'needs_you', check: 'x', detail: "the respondent would read a scripter's note" },
            { qid: 'S6_2', agent: 'qa', status: 'needs_you', check: 'QID_RENAMED', detail: 'the document names two questions S6' },
          ],
        }}
        secondLook={{
          checked: 31, fixed: 0, ready: 0, explained: 1, needs_you: 1,
          entries: [
            { qid: 'S6_2', status: 'explained', summary: 'Saved as S6_2.', source: 'rule' },
            { qid: 'Q23', status: 'needs_you', summary: 'Note on screen.', source: 'rule' },
          ],
        }}
      />,
    )
    expect(screen.getByText('1 needs you')).toBeTruthy()
    expect(screen.queryByText(/the document names two questions S6/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Q23' })).toBeTruthy()
  })
})
