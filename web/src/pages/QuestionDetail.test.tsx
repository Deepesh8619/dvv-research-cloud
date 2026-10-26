import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

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

describe('blank-question triage', () => {
  it('explains a single-answer question read with no options', () => {
    renderDetail(question({ type: 'single', options: [] }))
    expect(
      screen.getByText(/Nothing here was read as an answer list/),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: '+ Options from…' })).toBeTruthy()
  })

  it('stays quiet for a question that has options', () => {
    renderDetail(question({ type: 'single', options: [{ code: 1, label: 'Yes' }] }))
    expect(screen.queryByText(/Nothing here was read as an answer list/)).toBeNull()
  })

  it('stays quiet for a type that never carries an answer list', () => {
    renderDetail(question({ type: 'open_text', options: [] }))
    expect(screen.queryByText(/Nothing here was read as an answer list/)).toBeNull()
  })

  it('does not pile onto a derived variable, which is blank on purpose', () => {
    renderDetail(question({ type: 'single', options: [], flags: ['DERIVED_VARIABLE'] }))
    expect(screen.queryByText(/Nothing here was read as an answer list/)).toBeNull()
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

describe('saving a hidden variable', () => {
  it('keeps its codes and their rules (they used to be sent as an empty list)', async () => {
    const { questionnaireApi } = await import('../api/client')
    const calls: Array<Record<string, unknown>> = []
    const spy = vi.spyOn(questionnaireApi, 'update').mockImplementation(async (_s, _q, patch) => {
      calls.push(patch as Record<string, unknown>)
      return { data: question() } as never
    })
    const user = userEvent.setup()
    renderDetail(question({
      qid: 'D2a', type: 'info', text: 'CODE SIZE OF HOUSEHOLD', flags: ['DERIVED_VARIABLE'],
      options: [
        { code: 1, label: '1', derive_if: { op: 'eq', qid: 'D2', value: 1 } },
        { code: 4, label: '4 or more' },
      ] as never,
    }))
    await user.type(screen.getByDisplayValue('CODE SIZE OF HOUSEHOLD'), ' ')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    const options = calls[0].options as Array<Record<string, unknown>>
    expect(options.map((o) => o.code)).toEqual([1, 4])
    expect(options[0].derive_if).toEqual({ op: 'eq', qid: 'D2', value: 1 })
    spy.mockRestore()
  })
})

describe('re-typing a question as a rating grid', () => {
  it('shows rows and columns to fill in, and saves them with the shuffle setting', async () => {
    const { questionnaireApi } = await import('../api/client')
    const calls: Array<Record<string, unknown>> = []
    const spy = vi.spyOn(questionnaireApi, 'update').mockImplementation(async (_s, _q, patch) => {
      calls.push(patch as Record<string, unknown>)
      return { data: question() } as never
    })
    const user = userEvent.setup()
    // S8 as the parser read it: one answer whose single option holds every column.
    renderDetail(question({ qid: 'S8', type: 'single', text: 'When did you last dine in at each?',
      options: [{ code: 1, label: 'Past 30 days | 1-3 months ago' }] }))
    await user.selectOptions(screen.getByDisplayValue('One answer'), 'grid_single')
    const grid = screen.getByTestId('grid-editor')
    expect(grid.textContent).toContain('None yet')

    const adds = screen.getAllByRole('button', { name: '+ Add' })
    await user.click(adds[0])
    await user.type(screen.getByLabelText(/Rows .* label 1/), 'Pizza')
    await user.click(adds[0])
    await user.type(screen.getByLabelText(/Rows .* label 2/), 'Steakhouse')
    await user.click(adds[1])
    await user.type(screen.getByLabelText(/Columns .* label 1/), 'Past 30 days')
    await user.click(adds[1])
    await user.type(screen.getByLabelText(/Columns .* label 2/), 'Never')
    await user.click(screen.getByLabelText('Shuffle the rows for each respondent'))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(calls[0].type).toBe('grid_single')
    expect(calls[0].rows).toEqual([{ code: 1, label: 'Pizza' }, { code: 2, label: 'Steakhouse' }])
    expect(calls[0].cols).toEqual([{ code: 1, label: 'Past 30 days' }, { code: 2, label: 'Never' }])
    expect(calls[0].randomize_options).toBe(true)
    expect(calls[0].options).toEqual([])  // the wrong single option does not survive as an answer
    spy.mockRestore()
  })
})

describe('leaving a question out, deleting it, and its routing text', () => {
  it('puts a TERMINATE sentence beside the screen-out, not under "show this question if"', () => {
    renderDetail(question({ validation: { routing_verbatim: 'TERMINATE IF Bar & Grill IS NEVER' } }))
    const blocks = screen.getAllByText('TERMINATE IF Bar & Grill IS NEVER')
    expect(blocks).toHaveLength(1)
  })

  it('switches a question out of the link and shows who depends on it when refused', async () => {
    const { questionnaireApi } = await import('../api/client')
    const spy = vi.spyOn(questionnaireApi, 'setInLink').mockRejectedValueOnce({ response: { data: { detail: {
      error: "Q1 can't be left out of the link while other questions use its answer", referenced_by: ['Q3'] } } } })
    const user = userEvent.setup()
    renderDetail()
    await user.click(screen.getByLabelText('Ask this in the link'))
    expect(spy).toHaveBeenCalledWith('study_1', 'Q1', false)
    expect(await screen.findByText(/use its answer: Q3\. Change their routing first\./)).toBeTruthy()
    spy.mockRestore()
  })

  it('deletes only after the second step', async () => {
    const { questionnaireApi } = await import('../api/client')
    const spy = vi.spyOn(questionnaireApi, 'deleteQuestion').mockResolvedValue({ data: {} } as never)
    const onDeleted = vi.fn()
    const user = userEvent.setup()
    render(
      <QuestionDetail studyId="study_1" q={question()} priorQids={['Q1']} canWrite canCorrect={false}
        profile={null} onSaved={() => {}} onApproved={() => {}} onSplit={() => {}} onDeleted={onDeleted} />,
    )
    await user.click(screen.getByRole('button', { name: 'Delete question' }))
    expect(spy).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Yes, delete' }))
    expect(spy).toHaveBeenCalledWith('study_1', 'Q1')
    expect(onDeleted).toHaveBeenCalled()
    spy.mockRestore()
  })
})
