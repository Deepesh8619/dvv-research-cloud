import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { Question } from '../api/client'
import { QuestionDetail } from './QuestionnairePage'

/**
 * Guards the option-code box.
 *
 * `Number("")` is 0, so an input that coerced on every keystroke turned
 * "clear the box to retype the code" into "write code 0 into the
 * questionnaire" — silently, and only while the box was mid-edit. Nothing in
 * the component's props shows that; it is only visible by clearing a real
 * input and looking at what it holds before you finish typing.
 */

function question(overrides: Partial<Question> = {}): Question {
  return {
    qid: 'Q1',
    text: 'Which of these apply?',
    type: 'multi',
    options: [
      { code: 1, label: 'Alpha' },
      { code: 98, label: 'None of the above' },
    ],
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

function renderDetail(q: Question = question()) {
  return render(
    <QuestionDetail
      studyId="study_1"
      q={q}
      priorQids={['Q1']}
      canWrite
      // Keeps the founder-only surfaces (correction box, teach panel) out of
      // the tree, so this test needs no providers and no network.
      canCorrect={false}
      profile={null}
      onSaved={() => {}}
      onApproved={() => {}}
      onSplit={() => {}}
    />,
  )
}

describe('option code box', () => {
  it('stays empty while being cleared instead of snapping to 0', async () => {
    const user = userEvent.setup()
    renderDetail()

    const code = screen.getAllByLabelText('option code')[0] as HTMLInputElement
    expect(code.value).toBe('1')

    await user.clear(code)

    // The regression: this read "0", and tabbing away would have saved code 0.
    expect(code.value).toBe('')
  })

  it('restores the stored code when you leave without typing a new one', async () => {
    const user = userEvent.setup()
    renderDetail()

    const code = screen.getAllByLabelText('option code')[0] as HTMLInputElement
    await user.clear(code)
    await user.click(screen.getAllByLabelText('option label')[0])

    expect(code.value).toBe('1')
  })

  it('accepts a code typed after clearing', async () => {
    const user = userEvent.setup()
    renderDetail()

    const code = screen.getAllByLabelText('option code')[0] as HTMLInputElement
    await user.clear(code)
    await user.type(code, '97')
    await user.click(screen.getAllByLabelText('option label')[0])

    expect(code.value).toBe('97')
  })

  it('edits one option code without disturbing the others', async () => {
    const user = userEvent.setup()
    renderDetail()

    const codes = () => screen.getAllByLabelText('option code') as HTMLInputElement[]
    await user.clear(codes()[0])
    await user.type(codes()[0], '5')
    await user.click(screen.getAllByLabelText('option label')[0])

    expect(codes()[0].value).toBe('5')
    expect(codes()[1].value).toBe('98')
  })
})

describe('constant sum editor', () => {
  it('shows no items editor for a plain numeric question', () => {
    renderDetail(question({ type: 'numeric', rows: null, options: [] }))
    expect(screen.queryByText('Items')).toBeNull()
  })

  it('picking "Adds up to (constant sum)" reveals the items editor', async () => {
    const user = userEvent.setup()
    renderDetail(question({ type: 'numeric', rows: null, options: [] }))

    await user.selectOptions(screen.getByRole('combobox'), 'constant_sum')

    expect(screen.getByText('Items')).toBeTruthy()
    const total = screen.getByLabelText('Must total') as HTMLInputElement
    expect(total.value).toBe('100')
  })

  it('loads an existing constant-sum question pre-filled', () => {
    renderDetail(
      question({
        type: 'numeric',
        options: [],
        rows: [
          { code: 1, label: 'Brands I already trust' },
          { code: 2, label: 'Brands new to me' },
        ],
        validation: { min: 0, max: 100, sum: 100 },
      }),
    )

    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('constant_sum')
    expect(screen.getByDisplayValue('Brands I already trust')).toBeTruthy()
    expect(screen.getByDisplayValue('Brands new to me')).toBeTruthy()
    const total = screen.getByLabelText('Must total') as HTMLInputElement
    expect(total.value).toBe('100')
  })
})

describe('approving after a change', () => {
  it('lets an approved question be approved again once it carries a blocking flag', () => {
    // A re-read, a split or an edited route can flag a question a reviewer
    // already signed off. Approving is what CLEARS a blocking flag, so a
    // disabled "Approved" left Finalize refusing forever.
    renderDetail(question({ approved: true, flags: ['AMBIGUOUS_ROUTING'] }))
    const button = screen.getByRole('button', { name: /Re-approve/ }) as HTMLButtonElement
    expect(button.disabled).toBe(false)
  })

  it('still shows a clean approved question as done', () => {
    renderDetail(question({ approved: true, flags: [] }))
    const button = screen.getByRole('button', { name: 'Approved' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })
})
