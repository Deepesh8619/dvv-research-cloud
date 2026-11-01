import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import DecodingPanel from './DecodingPanel'
import type { NoteRecord } from '../../api/client'

vi.mock('../../api/client', () => ({
  questionnaireApi: {
    addQuestion: vi.fn().mockResolvedValue({ data: {} }),
    decideReading: vi.fn().mockResolvedValue({ data: {} }),
  },
}))
vi.mock('../CopilotDrawer', () => ({ askCopilot: vi.fn() }))

const report = {
  verdict: 'unfamiliar' as const,
  questions: 10,
  read_by_skills: 7,
  read_share: 0.7,
  skills: [{ key: 'prose', name: 'Written questions', questions: 7, examples: ['Q1'] }],
  not_read: ['Q8', 'Q9'],
  teach: [{ fragment: 'SHOWCARD', questions: 3, examples: ['Q2'], suggest: 'type_signal' }],
}

describe('DecodingPanel', () => {
  it('announces an unfamiliar document and links what to look at', () => {
    const onGoTo = vi.fn()
    render(<DecodingPanel decoding={report} onGoTo={onGoTo} />)
    expect(screen.getByText(/7 of 10 questions read by known skills/)).toBeTruthy()
    expect(screen.getByText(/unfamiliar format/)).toBeTruthy()
    expect(screen.getByText('SHOWCARD')).toBeTruthy()
    fireEvent.click(screen.getByText('Q8'))
    expect(onGoTo).toHaveBeenCalledWith('Q8')
  })

  it('shows what the brief asks for, missing first, with links', () => {
    const onGoTo = vi.fn()
    const brief = {
      items: [
        { kind: 'quota' as const, text: 'Micro 2-9 Employees', status: 'covered' as const, where: ['SS_EMP'], why: 'asked' },
        { kind: 'variable' as const, text: 'C3: CODE GENDER', status: 'missing' as const, where: ['C3'], why: 'read as asked' },
      ],
      covered: 1,
      missing: 1,
      check: 0,
      screens: ['INTRO'],
      conventions: { label_families: { Q: 40, S: 9 }, pn_markers: { PN: 12 } },
    }
    render(<DecodingPanel decoding={{ ...report, verdict: 'familiar', brief }} onGoTo={onGoTo} />)
    expect(screen.getByText(/1 met · 1 missing · 0 to confirm/)).toBeTruthy()
    expect(screen.getByText(/Q \(40\), S \(9\)/)).toBeTruthy()
    const rows = screen.getAllByRole('listitem').filter((li) => li.textContent?.includes('Employees') || li.textContent?.includes('GENDER'))
    expect(rows[0].textContent).toContain('C3: CODE GENDER')
    fireEvent.click(screen.getByText('C3'))
    expect(onGoTo).toHaveBeenCalledWith('C3')
  })

  it('asks what only a person can settle and creates the ready variables', async () => {
    const { askCopilot } = await import('../CopilotDrawer')
    const { questionnaireApi } = await import('../../api/client')
    const analyst = {
      variables: [
        { name: 'TENURE', at: 'S8', said: 'TENURE', status: 'ready' as const, why: '',
          bands: [{ label: '<6M', line: '<6M: punch 1 at S5d', condition: { op: 'in', qid: 'S5d', values: [1] } }] },
        { name: 'PARENTBRAND', at: 'S8', said: 'using excel file', status: 'needs_you' as const,
          why: 'built from a file that is not attached', bands: [] },
      ],
      ready: 1,
      questions: [{ qid: 'S6', kind: 'codes', key: 'k1', ask: "S6's code box does not match its answers.",
                    options: ['Number the answers in order', 'I will set the codes'] }],
      checks: [{ qid: 'S5', kind: 'quality' as const, said: 'flag if more than 3 responses' }],
    }
    render(<DecodingPanel decoding={{ ...report, verdict: 'familiar', analyst }} onGoTo={() => {}} studyId="s1" />)
    fireEvent.click(screen.getByText('Number the answers in order'))
    expect(askCopilot).toHaveBeenCalledWith("S6's code box does not match its answers.\nMy answer: Number the answers in order")
    expect(questionnaireApi.decideReading).toHaveBeenCalledWith(
      's1', 'k1', "S6's code box does not match its answers.", 'Number the answers in order')
    expect(screen.getByText('Number the answers in order').getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByText('Create the 1 ready variables'))
    expect(await screen.findByText(/Created 1/)).toBeTruthy()
    expect(questionnaireApi.addQuestion).toHaveBeenCalledWith('s1', expect.objectContaining({
      qid: 'HID_TENURE', hidden: true, type: 'info',
      options: [{ code: 1, label: '<6M', derive_if: { op: 'in', qid: 'S5d', values: [1] } }],
    }))
  })

  it('counts distinct notes by best outcome, filters, and explains an unread one', async () => {
    const { askCopilot } = await import('../CopilotDrawer')
    const rec = (qid: string, said: string, kind: string, outcome: NoteRecord['outcome']) => ({ qid, said, kind, outcome, detail: '' })
    const noteLedger = { records: [
      rec('Q1', 'Ask all', 'ask_all', 'by_design'),
      rec('Q2', 'Show the first card', 'layout', 'scripter'),
      rec('Q2', 'Show the first card', 'stimulus', 'unread'), // same note, two kinds: counts once
      rec('Q3', 'Wibble the answers', 'none', 'unread'),
      rec('Q4', 'Keep last', 'anchor', 'applied'),
    ] }
    render(<DecodingPanel decoding={{ ...report, verdict: 'familiar' }} noteLedger={noteLedger} onGoTo={() => {}} />)
    expect(screen.getByText('4 notes: 1 applied, 1 for the scripter, 1 need nothing, 1 not understood')).toBeTruthy()
    expect(screen.getByTestId('note-rows').textContent).not.toMatch(/layout|stimulus|ask_all|anchor/)
    fireEvent.click(screen.getByText('Not understood (1)'))
    expect(screen.getByTestId('note-rows').querySelectorAll('li').length).toBe(1)
    fireEvent.click(screen.getByText('Explain this to the Copilot'))
    expect(askCopilot).toHaveBeenCalledWith(expect.stringContaining('Wibble the answers'))
  })

  it('shows no notes section for a project without a ledger', () => {
    render(<DecodingPanel decoding={{ ...report, verdict: 'familiar' }} noteLedger={null} onGoTo={() => {}} />)
    expect(screen.queryByText('Notes')).toBeNull()
  })

  it('renders nothing before a report exists', () => {
    const { container } = render(<DecodingPanel decoding={null} onGoTo={() => {}} />)
    expect(container.innerHTML).toBe('')
  })
})
