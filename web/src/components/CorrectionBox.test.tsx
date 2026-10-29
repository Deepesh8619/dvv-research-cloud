import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import CorrectionBox from './CorrectionBox'

vi.mock('./PreviewDiff', () => ({ default: () => null }))

vi.mock('../api/client', () => ({
  correctionsApi: {
    suggestions: vi.fn().mockResolvedValue({ data: { suggestions: [] } }),
    rules: vi.fn().mockResolvedValue({ data: { rules: [] } }),
    create: vi.fn().mockResolvedValue({
      data: {
        id: 'corr_1', study_id: 's1', question_id: 'QLANG',
        instruction: 'the "[ALSO]" line is the question translated',
        scope: 'profile', profile_slug: 'gbk', status: 'resolved', lane: 'live',
        rule_text: 'Read a line marked [ALSO] as the question in another language.',
        rationale: 'It is how this client writes bilingual questions.',
        degraded: false, created_at: null,
        pattern: { kind: 'type_signal', pattern: '[ALSO]', value: 'info', meaning: 'a marker stating the answer type',
                   affects: ['QLANG', 'QHHLANG'] },
        similar: [{ id: 'rule_9', rule_text: 'Spanish lines are translations.', profile_slug: 'gbk',
                    active: false, similarity: 0.81 }],
      },
    }),
    applyRule: vi.fn().mockResolvedValue({ data: { id: 'r1', applied_to_current: { n_updated: 2 } } }),
    toggleRule: vi.fn(),
  },
}))

describe('CorrectionBox', () => {
  it('shows the question as written, lets a fragment be pointed at, and teaches the reader in one click', async () => {
    const { correctionsApi } = await import('../api/client')
    render(
      <CorrectionBox studyId="s1" questionId="QLANG" profileSlug="gbk"
        sourceText={'Which language?\n[ALSO] ¿En qué idioma?\n1 English'} />,
    )
    fireEvent.click(screen.getByText('Correct this'))
    expect(screen.getByText('As the document writes it')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '[ALSO]' }))
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(box.value).toBe('"[ALSO]" ')

    fireEvent.change(box, { target: { value: 'the "[ALSO]" line is the question translated' } })
    fireEvent.click(screen.getByText('Send correction'))

    expect(await screen.findByText('The reader can learn this directly')).toBeTruthy()
    expect(screen.getByText(/Found in 2 questions of\s+this document: QLANG, QHHLANG/)).toBeTruthy()
    expect(screen.getByText('Already taught something similar')).toBeTruthy()
    expect(screen.getByText(/gbk, switched off/)).toBeTruthy()

    fireEvent.click(screen.getByText('Teach the reader'))
    await waitFor(() => expect(correctionsApi.applyRule).toHaveBeenCalledWith('gbk', {
      pattern_kind: 'type_signal', pattern: '[ALSO]', pattern_value: 'info', scope: 'profile',
      correction_id: 'corr_1', study_id: 's1',
    }))
    expect(await screen.findByText(/Taught\. 2 question\(s\) in this document re-read with it\./)).toBeTruthy()
  })
})
