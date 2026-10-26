import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Question } from '../api/client'

const { QUESTIONS } = vi.hoisted(() => {
  const q = (qid: string, extra: Partial<Question> = {}): Question => ({
  qid, text: `${qid} text`, type: 'single', rows: null, cols: null, required: true,
  randomize_options: false, show_if: null, terminate_if: null, piping_refs: [], validation: null,
  source_text: null, source_location: null, extraction_confidence: {}, flags: [], approved: false,
  options: [{ code: 1, label: 'Man' }, { code: 2, label: 'Woman' }] as Question['options'],
  ...extra,
})

  const QUESTIONS = [
  q('S1'),
  q('Q2', { show_if: { op: 'eq', qid: 'S1', value: 2 } }),
  q('Q3', { type: 'numeric', options: [] }),
  q('Q4'),
]

  return { q, QUESTIONS }
})

vi.mock('../api/client', () => ({
  questionnaireApi: {
    questions: vi.fn().mockResolvedValue({ data: { questions: QUESTIONS } }),
    logic: vi.fn().mockResolvedValue({ data: { proof: { unreachable: [{ qid: 'Q4', why: 'Q4 needs S1 = 7, but S1 only allows 1–2' }], not_provable: [] } } }),
    update: vi.fn().mockResolvedValue({ data: {} }),
  },
}))

import { questionnaireApi } from '../api/client'
import LogicMap, { combine, conditionFor, edgesOf } from './LogicMap'

describe('LogicMap helpers', () => {
  it('draws an edge from the question depended on to the one that depends', () => {
    expect(edgesOf(QUESTIONS)).toEqual([{ from: 'S1', to: 'Q2', kind: 'shows' }])
  })

  it('builds the condition a ticked answer means, by question type', () => {
    expect(conditionFor(QUESTIONS[0], [2], 'gt', '')).toEqual({ op: 'eq', qid: 'S1', value: 2 })
    expect(conditionFor(QUESTIONS[0], [1, 2], 'gt', '')).toEqual({ op: 'in', qid: 'S1', values: [1, 2] })
    expect(conditionFor({ ...QUESTIONS[0], type: 'multi' }, [1, 2], 'gt', '')).toEqual(
      { op: 'or', children: [{ op: 'selected', qid: 'S1', value: 1 }, { op: 'selected', qid: 'S1', value: 2 }] })
    expect(conditionFor(QUESTIONS[2], [], 'gt', '17')).toEqual({ op: 'gt', qid: 'Q3', value: 17 })
    expect(conditionFor(QUESTIONS[2], [], 'gt', '')).toBeNull()
    expect(conditionFor(QUESTIONS[0], [], 'gt', '')).toBeNull()
  })

  it('adds to an existing condition with and, or replaces it', () => {
    const a = { op: 'eq' as const, qid: 'S1', value: 2 }
    const b = { op: 'eq' as const, qid: 'Q3', value: 5 }
    expect(combine(a, b, 'add')).toEqual({ op: 'and', children: [a, b] })
    expect(combine({ op: 'and', children: [a] }, b, 'add')).toEqual({ op: 'and', children: [a, b] })
    expect(combine(a, b, 'replace')).toEqual(b)
    expect(combine(null, b, 'add')).toEqual(b)
  })
})

describe('LogicMap', () => {
  it('shows what the solver proved and connects two questions with a condition', async () => {
    render(<LogicMap studyId="s" canWrite />)
    expect(await screen.findByText('Solver: 1 question can never be shown.')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Q4: .*Can never be shown/ })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /^S1:/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Connect a condition from here' }))
    fireEvent.click(screen.getByRole('button', { name: /^Q4:/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Woman' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save condition' }))
    await waitFor(() => expect(questionnaireApi.update).toHaveBeenCalledWith(
      's', 'Q4', { show_if: { op: 'eq', qid: 'S1', value: 2 } }))
  })

  it('removes a condition only after the second, explicit step', async () => {
    render(<LogicMap studyId="s" canWrite />)
    fireEvent.click(await screen.findByRole('button', { name: /^Q2:/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove its condition' }))
    expect(questionnaireApi.update).not.toHaveBeenCalledWith('s', 'Q2', { show_if: null })
    fireEvent.click(screen.getByRole('button', { name: 'Yes, remove' }))
    await waitFor(() => expect(questionnaireApi.update).toHaveBeenCalledWith('s', 'Q2', { show_if: null }))
  })

  it('offers no editing to a read-only role', async () => {
    render(<LogicMap studyId="s" canWrite={false} />)
    fireEvent.click(await screen.findByRole('button', { name: /^S1:/ }))
    expect(screen.queryByRole('button', { name: 'Connect a condition from here' })).toBeNull()
  })
})
