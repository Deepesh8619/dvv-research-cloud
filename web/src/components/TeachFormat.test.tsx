import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Question } from '../api/client'
import TeachFormat from './TeachFormat'

vi.mock('../api/client', () => ({
  correctionsApi: {
    patternKinds: vi.fn().mockResolvedValue({
      data: { kinds: [{ kind: 'type_signal', label: 'Answer type', help: '', values: ['single'] }], all_kinds: [] },
    }),
    applyRule: vi.fn(),
  },
  QUESTION_TYPES: ['single', 'multi'],
}))

function question(overrides: Partial<Question> = {}): Question {
  return {
    qid: 'Q1',
    text: 'text',
    type: 'single',
    options: [],
    rows: null,
    cols: null,
    required: true,
    randomize_options: false,
    show_if: null,
    terminate_if: null,
    piping_refs: [],
    validation: null,
    source_text: null,
    source_location: null,
    extraction_confidence: {},
    flags: [],
    approved: false,
    ...overrides,
  } as Question
}

describe('TeachFormat', () => {
  it('opens pre-filled with the exact fragment when requested from elsewhere on the page', async () => {
    render(
      <TeachFormat
        profileSlug="acme"
        studyId="S1"
        question={question()}
        openRequest={{ fragment: '[SHOWCARD]', nonce: 1 }}
      />,
    )
    const input = (await screen.findByPlaceholderText('e.g. [SHOWCARD]')) as HTMLInputElement
    await waitFor(() => expect(input.value).toBe('[SHOWCARD]'))
  })

  it('stays closed with no request', () => {
    render(<TeachFormat profileSlug="acme"
        studyId="S1" question={question()} />)
    expect(screen.queryByPlaceholderText('e.g. [SHOWCARD]')).toBeNull()
    expect(screen.getByRole('button', { name: /Teach this format/ })).toBeTruthy()
  })
})
