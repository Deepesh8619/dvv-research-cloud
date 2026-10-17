import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import ResolutionCard, { SecondLookSummary } from './ResolutionCard'
import type { Question, Resolution } from '../../api/client'

const proposed = { qid: 'DA_VID_EFFIC', type: 'grid_single', text: 'How effective?' } as unknown as Question

describe('ResolutionCard', () => {
  it('says what was found in one sentence and puts a ready fix in the editor', () => {
    const onUse = vi.fn()
    const r: Resolution = {
      status: 'ready',
      summary: 'Each answer chosen at DA_VID_MEASURE becomes a row, rated on the 10-point scale.',
      detail: 'The document names this question itself — a copy slip.',
      source: 'rule',
      issues: [],
      proposed,
    }
    render(<ResolutionCard resolution={r} canWrite onUse={onUse} details={<p>EMPTY_OPTIONS</p>} />)
    expect(screen.getByText('Fix ready')).toBeTruthy()
    expect(screen.queryByText('EMPTY_OPTIONS')).toBeNull() // technical detail folded away
    fireEvent.click(screen.getByText('Apply this fix'))
    expect(onUse).toHaveBeenCalledWith(proposed)
    expect(screen.getByText('In the editor — save to keep')).toBeTruthy()
    fireEvent.click(screen.getByText('Technical details'))
    expect(screen.getByText('EMPTY_OPTIONS')).toBeTruthy()
  })

  it('offers Undo on an automatic fix, and no button to a reader who cannot edit', () => {
    const r: Resolution = { status: 'fixed', summary: 'Fixed.', detail: '', source: 'ai:bedrock/qwen3', issues: [], before: proposed }
    const { rerender } = render(<ResolutionCard resolution={r} canWrite onUse={() => {}} />)
    expect(screen.getByText('Undo')).toBeTruthy()
    expect(screen.getByText('worked out by AI (bedrock/qwen3)')).toBeTruthy()
    rerender(<ResolutionCard resolution={r} canWrite={false} onUse={() => {}} />)
    expect(screen.queryByText('Undo')).toBeNull()
  })

  it('sums up the document in one line', () => {
    render(<SecondLookSummary record={{ checked: 79, fixed: 10, ready: 4, explained: 3, needs_you: 12, entries: [] }} />)
    expect(screen.getByTestId('second-look-summary').textContent).toBe(
      'Second look: 10 fixed automatically · 4 fixes ready to apply · 3 notes · 12 need you',
    )
  })
})
