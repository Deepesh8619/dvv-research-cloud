import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import type { DryRunReport } from '../api/client'
import LogicXray from './LogicXray'

const report = {
  n_respondents: 100,
  seed: 1,
  passed: false,
  dispositions: { complete: 70, terminate: 30, overquota: 0 },
  conditions: [],
  never_fired: [],
  impossible: [],
  display_issues: [],
  unenforced_routing: [],
  questions_never_shown: ['Q9'],
  paths: [
    { qids: ['S1', 'Q1', 'Q2'], n: 70, disposition: 'complete' },
    { qids: ['S1'], n: 30, disposition: 'terminate' },
  ],
  transitions: [
    { from: 'S1', to: 'Q1', n: 70 },
    { from: 'S1', to: 'END', n: 30 },
    { from: 'Q1', to: 'Q2', n: 70 },
    { from: 'Q2', to: 'END', n: 70 },
  ],
  exits: [{ qid: 'S1', disposition: 'terminate', n: 30 }],
} as DryRunReport

describe('LogicXray', () => {
  it('draws stations, a skip arc, an exit and a dashed unreachable station', () => {
    render(<LogicXray report={report} />)
    expect(screen.getAllByTestId('station')).toHaveLength(5) // S1 Q1 Q2 Q9 END
    expect(screen.getAllByTestId('arc').length).toBeGreaterThan(0)
    expect(screen.getAllByTestId('exit')).toHaveLength(1)
    expect(screen.getByText('TERMINATE 30')).toBeTruthy()
    const dead = screen.getAllByTestId('station').filter((s) => s.dataset.unreachable)
    expect(dead).toHaveLength(1)
    expect(dead[0].querySelector('circle')?.getAttribute('stroke-dasharray')).toBeTruthy()
  })

  it('selecting a station updates the panel', () => {
    render(<LogicXray report={report} />)
    fireEvent.click(screen.getByLabelText('Q1'))
    expect(screen.getByTestId('xray-panel').textContent).toContain('70% of respondents reach it')
    expect(screen.getByTestId('xray-panel').textContent).toContain('then Q2: 70')
  })
})

describe('stationOrder', () => {
  it('puts a question most people skip in questionnaire order, not after what follows it', async () => {
    const { stationOrder } = await import('./LogicXray')
    const report = {
      n_respondents: 10, questions_never_shown: ['Q9'],
      paths: [{ qids: ['A', 'B', 'D'], n: 7, disposition: 'complete' },
              { qids: ['A', 'B', 'C', 'D'], n: 3, disposition: 'complete' }],
      transitions: [{ from: 'A', to: 'B', n: 10 }, { from: 'B', to: 'D', n: 7 },
                    { from: 'B', to: 'C', n: 3 }, { from: 'C', to: 'D', n: 3 }, { from: 'D', to: 'END', n: 10 }],
    } as unknown as Parameters<typeof stationOrder>[0]
    expect(stationOrder(report)).toEqual(['A', 'B', 'C', 'D', 'Q9', 'END'])
  })
})
