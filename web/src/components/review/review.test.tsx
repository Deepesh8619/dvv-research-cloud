import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Question } from '../../api/client'
import Preflight from './Preflight'
import { ConnectionsPanel, IssuesPanel } from './QuestionMap'

vi.mock('../../api/client', () => ({
  fieldingApi: { preflight: vi.fn() },
  questionnaireApi: { setLinks: vi.fn() },
}))

async function api() {
  return (await import('../../api/client')) as unknown as {
    fieldingApi: { preflight: ReturnType<typeof vi.fn> }
    questionnaireApi: { setLinks: ReturnType<typeof vi.fn> }
  }
}

const check = (id: string, status: string, qids: string[] = []) => ({
  id, label: `${id} label`, status, detail: status === 'pass' ? '' : `${id} detail`, qids,
})

afterEach(() => vi.useRealTimers())

async function runPreflight(result: object) {
  vi.useFakeTimers()
  ;(await api()).fieldingApi.preflight.mockResolvedValue({ data: result })
  const onProceed = vi.fn()
  render(
    <MemoryRouter>
      <Preflight studyId="s" creating={false} onProceed={onProceed} onCancel={vi.fn()} />
    </MemoryRouter>,
  )
  await act(async () => {
    await Promise.resolve() // the check's response arrives
  })
  // Each check lands on its own timer, set after the previous one renders.
  for (let i = 0; i < 12; i++) {
    await act(async () => {
      vi.advanceTimersByTime(200)
    })
  }
  return onProceed
}

describe('the pre-link check', () => {
  it('opens the link on its own when everything passes', async () => {
    const onProceed = await runPreflight({ ok: true, n_fail: 0, n_warn: 0, checks: [check('a', 'pass')] })
    expect(onProceed).toHaveBeenCalledWith(false)
  })

  it('asks the reviewer to look at warnings before continuing', async () => {
    const onProceed = await runPreflight({
      ok: true, n_fail: 0, n_warn: 1, checks: [check('a', 'pass'), check('guesses', 'warn', ['Q8'])],
    })
    expect(onProceed).not.toHaveBeenCalled()
    const create = screen.getByRole('button', { name: 'Create link' }) as HTMLButtonElement
    expect(create.disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(create)
    expect(onProceed).toHaveBeenCalledWith(false)
  })

  it('names the failing questions and opens only if overridden on purpose', async () => {
    const onProceed = await runPreflight({
      ok: false, n_fail: 1, n_warn: 0, checks: [check('answers', 'fail', ['Q2c', 'Q9'])],
    })
    const link = screen.getByRole('link', { name: 'Q2c' }) as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/projects/s/questionnaire?q=Q2c')
    fireEvent.click(screen.getByRole('button', { name: 'Create anyway' }))
    expect(onProceed).toHaveBeenCalledWith(true)
  })
})

const LINKS = {
  asked_if: ['S1'], asked_if_text: null, screens_out_on: [], options_from: [], text_from: [],
  computed_from: [], loop_of: null, used_by: ['Q9'],
}

function question(qid: string, validation: Record<string, unknown> | null = null): Question {
  return {
    qid, text: `${qid} text`, type: 'multi', options: [], rows: null, cols: null, required: true,
    randomize_options: false, show_if: null, terminate_if: null, piping_refs: [], validation,
    source_text: null, source_location: null, extraction_confidence: {}, flags: [], approved: false,
  }
}

describe('connections', () => {
  it('shows what the question depends on and what it decides, and adds a link', async () => {
    const { questionnaireApi } = await api()
    questionnaireApi.setLinks.mockResolvedValue({ data: question('Q2b') })
    const onGoTo = vi.fn()
    render(
      <ConnectionsPanel
        studyId="s" question={question('Q2b')}
        entry={{ bucket: 'core', reason: '', suggested: false, links: LINKS }}
        earlier={[question('S1'), question('Q2a')]} canWrite onChanged={vi.fn()} onGoTo={onGoTo}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Q9' }))
    expect(onGoTo).toHaveBeenCalledWith('Q9')

    fireEvent.click(screen.getByRole('button', { name: '+ Options from…' }))
    fireEvent.change(screen.getByLabelText('Source question'), { target: { value: 'Q2a' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add link' }))
    await waitFor(() =>
      expect(questionnaireApi.setLinks).toHaveBeenCalledWith('s', 'Q2b', {
        options_from: [{ source_qid: 'Q2a', mode: 'selected' }],
      }),
    )
  })

  it('removes a link with its ×', async () => {
    const { questionnaireApi } = await api()
    questionnaireApi.setLinks.mockClear()
    questionnaireApi.setLinks.mockResolvedValue({ data: question('Q3') })
    const withPipe = question('Q3', { techniques: { pipes: [{ source_qid: 'Q2a' }] } })
    render(
      <ConnectionsPanel
        studyId="s" question={withPipe}
        entry={{ bucket: 'core', reason: '', suggested: false, links: { ...LINKS, text_from: ['Q2a'] } }}
        earlier={[]} canWrite onChanged={vi.fn()} onGoTo={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByLabelText('Remove link to Q2a'))
    await waitFor(() =>
      expect(questionnaireApi.setLinks).toHaveBeenCalledWith('s', 'Q3', { text_from: [] }),
    )
  })
})

describe('issues', () => {
  it('opens the question an issue is about', () => {
    const onGoTo = vi.fn()
    render(<IssuesPanel errors={['question Q2c: grid_single requires non-empty rows']} onGoTo={onGoTo} />)
    fireEvent.click(screen.getByRole('button', { name: /Q2c/ }))
    expect(onGoTo).toHaveBeenCalledWith('Q2c')
    expect(screen.getByText('grid single requires non-empty rows')).toBeTruthy()
  })
})
