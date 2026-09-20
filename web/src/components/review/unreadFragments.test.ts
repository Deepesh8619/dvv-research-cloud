import { describe, expect, it } from 'vitest'

import type { Question } from '../../api/client'
import { unreadFragments } from './unreadFragments'

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

describe('unreadFragments', () => {
  it('returns directives and unrecognised row labels not cited in evidence', () => {
    const q = question({
      validation: {
        scripter_directives: ['[SHOWCARD]', '[ROTATE]'],
        unrecognised_rows: { 'a 11-point scale table': {} },
      },
      extraction_evidence: { type: '[ROTATE]' },
    })
    expect(unreadFragments(q)).toEqual(['[SHOWCARD]', 'a 11-point scale table'])
  })

  it('ignores fragments already cited in extraction_evidence', () => {
    const q = question({
      validation: { scripter_directives: ['[SHOWCARD]'] },
      extraction_evidence: { type: 'a [SHOWCARD] up top' },
    })
    expect(unreadFragments(q)).toEqual([])
  })

  it('is empty when there is nothing to teach', () => {
    expect(unreadFragments(question())).toEqual([])
  })
})
