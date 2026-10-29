import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import DecodingPanel from './DecodingPanel'

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

  it('renders nothing before a report exists', () => {
    const { container } = render(<DecodingPanel decoding={null} onGoTo={() => {}} />)
    expect(container.innerHTML).toBe('')
  })
})
