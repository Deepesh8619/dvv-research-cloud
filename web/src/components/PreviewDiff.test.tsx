import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { ExtractionDiff } from '../api/client'
import { ReadyView } from './PreviewDiff'

const diff: ExtractionDiff = {
  counts: { improved: 2, regressed: 1, changed: 0, unchanged: 5 },
  n_questions: 8,
  quality: {
    before: { n: 8, answerable_pct: 75, qa_pass_pct: 50 },
    after: { n: 8, answerable_pct: 100, qa_pass_pct: 88 },
  },
  questions: [
    { qid: 'Q9', verdict: 'regressed', only_in: null, flags_cleared: [], flags_added: [],
      changes: [{ field: 'answers', verdict: 'regressed', before: { options: ['Yes', 'No'] }, after: {} }] },
    { qid: 'Q6', verdict: 'improved', only_in: null, renumbered_from: 'Q5', flags_cleared: ['QA_ISSUE'], flags_added: [],
      changes: [{ field: 'answers', verdict: 'improved',
        before: { options: ['Safety |', 'Other (specify) [ANCHOR] |'] }, after: { options: ['Safety', 'Other (specify)'] } }] },
    { qid: 'Q5', verdict: 'improved', only_in: 'after', flags_cleared: [], flags_added: [], changes: [] },
  ],
}

describe('rule preview', () => {
  it('leads with accuracy, opens on what got worse, and keeps each question to one line', () => {
    render(<ReadyView diff={diff} />)
    expect(screen.getByText('Passes QA')).toBeTruthy()
    expect(screen.getByText('(+38)')).toBeTruthy()
    // Opens filtered to the regressions; the improvements wait behind their tab.
    expect(screen.queryByText('was Q5')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Better 2/ }))
    expect(screen.getByText('was Q5')).toBeTruthy()
    fireEvent.click(screen.getByText('was Q5'))
    expect(screen.getByText(/separators removed/)).toBeTruthy()
  })
})
