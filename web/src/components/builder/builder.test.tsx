import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Question } from '../../api/client'
import { suggestNextQid } from '../../pages/SurveyBuilderPage'
import { AddQuestionPalette } from './AddQuestionPalette'
import { PasteOptionsBox } from './PasteOptionsBox'
import { RespondentPreview } from './RespondentPreview'
import { defaultQuestionFor, parsePastedOptions } from './typeMeta'

function question(overrides: Partial<Question> = {}): Question {
  return {
    qid: 'Q1',
    text: 'Pick one',
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
  }
}

describe('parsePastedOptions', () => {
  it('keeps the code from "1 Label", "1. Label" and "1\\tLabel"', () => {
    const opts = parsePastedOptions('1 Strongly agree\n2. Agree\n3\tDisagree')
    expect(opts).toEqual([
      { code: 1, label: 'Strongly agree' },
      { code: 2, label: 'Agree' },
      { code: 3, label: 'Disagree' },
    ])
  })

  it('codes a bare list from 1, skipping blank lines', () => {
    const opts = parsePastedOptions('Yes\n\nNo\nMaybe')
    expect(opts).toEqual([
      { code: 1, label: 'Yes' },
      { code: 2, label: 'No' },
      { code: 3, label: 'Maybe' },
    ])
  })

  it('picks up bare lines after a coded run at the next free code', () => {
    const opts = parsePastedOptions('5 High\n6 Low\nOther')
    expect(opts).toEqual([
      { code: 5, label: 'High' },
      { code: 6, label: 'Low' },
      { code: 7, label: 'Other' },
    ])
  })
})

describe('PasteOptionsBox', () => {
  it('parses what was pasted and hands it to onApply', () => {
    const onApply = vi.fn()
    render(<PasteOptionsBox onApply={onApply} />)

    fireEvent.click(screen.getByRole('button', { name: 'Paste a list…' }))
    fireEvent.change(screen.getByLabelText(/One option per line/), {
      target: { value: '1 Red\n2 Blue' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add 2 options' }))

    expect(onApply).toHaveBeenCalledWith([
      { code: 1, label: 'Red' },
      { code: 2, label: 'Blue' },
    ])
  })
})

describe('suggestNextQid', () => {
  it('suggests one past the highest existing Q-number', () => {
    expect(suggestNextQid([question({ qid: 'Q1' }), question({ qid: 'Q3' })])).toBe('Q4')
  })

  it('starts at Q1 for an empty survey', () => {
    expect(suggestNextQid([])).toBe('Q1')
  })
})

describe('AddQuestionPalette', () => {
  it('inserts a sensible default for the picked card, keyed by suggestNextQid', () => {
    const onPick = vi.fn()
    render(<AddQuestionPalette onPick={onPick} />)

    fireEvent.click(screen.getByRole('button', { name: /One answer/ }))
    expect(onPick).toHaveBeenCalledWith('single')

    // What the page does with that key: a real Yes/No default, not an empty
    // shell, placed under the qid the same helper the list already uses.
    const spec = defaultQuestionFor('single')
    expect(spec.create.options).toEqual([{ code: 1, label: 'Yes' }, { code: 2, label: 'No' }])
    expect(suggestNextQid([question({ qid: 'Q1' })])).toBe('Q2')
  })

  it('renders a card for every named type, including "Adds up to"', () => {
    render(<AddQuestionPalette onPick={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Adds up to/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Rank order/ })).toBeTruthy()
  })
})

describe('RespondentPreview', () => {
  it('renders a rank question with the tap-to-rank control', () => {
    render(
      <RespondentPreview
        debounceMs={0}
        questions={[
          question({
            qid: 'R1',
            type: 'rank',
            options: [{ code: 1, label: 'Coffee' }, { code: 2, label: 'Tea' }],
          }),
        ]}
      />,
    )
    // TapToRank's own empty-state copy (components/respondent/controls.tsx) —
    // present only when SurveyJS actually mounted a `ranking` question through
    // our registered control, not its stock one.
    expect(screen.getByText('Tap your favourite first — it becomes #1')).toBeTruthy()
    expect(screen.getByText('Coffee')).toBeTruthy()
  })

  it('shows nothing-to-preview instead of an empty survey shell', () => {
    render(<RespondentPreview questions={[]} debounceMs={0} />)
    expect(screen.getByText('Nothing to preview yet.')).toBeTruthy()
  })
})
