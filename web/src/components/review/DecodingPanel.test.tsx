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

  it('renders nothing before a report exists', () => {
    const { container } = render(<DecodingPanel decoding={null} onGoTo={() => {}} />)
    expect(container.innerHTML).toBe('')
  })
})
