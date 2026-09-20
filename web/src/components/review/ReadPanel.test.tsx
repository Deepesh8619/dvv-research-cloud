import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Question } from '../../api/client'
import ReadPanel from './ReadPanel'

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

describe('ReadPanel', () => {
  it('renders both columns: what was cited, and what was left unread', () => {
    const q = question({
      extraction_evidence: { options: 'a 11-point scale table' },
      validation: { scripter_directives: ['[SHOWCARD]'] },
    })
    render(<ReadPanel question={q} canTeach={false} onTeach={vi.fn()} />)
    expect(screen.getByText('options:')).toBeTruthy()
    expect(screen.getByText('a 11-point scale table')).toBeTruthy()
    expect(screen.getByText('[SHOWCARD]')).toBeTruthy()
  })

  it('shows an empty state in the unread column once everything was read', () => {
    const q = question({ extraction_evidence: { type: '[SINGLE]' } })
    render(<ReadPanel question={q} canTeach={false} onTeach={vi.fn()} />)
    expect(screen.getByText('Everything in the source was read.')).toBeTruthy()
  })

  it('renders nothing at all for a clean question with no citations or leftovers', () => {
    const { container } = render(<ReadPanel question={question()} canTeach onTeach={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('opens Teach with the exact fragment when "Teach this" is clicked', () => {
    const q = question({ validation: { scripter_directives: ['[SHOWCARD]'] } })
    const onTeach = vi.fn()
    render(<ReadPanel question={q} canTeach onTeach={onTeach} />)
    fireEvent.click(screen.getByRole('button', { name: 'Teach this' }))
    expect(onTeach).toHaveBeenCalledWith('[SHOWCARD]')
  })

  it('hides "Teach this" when the viewer cannot teach', () => {
    const q = question({ validation: { scripter_directives: ['[SHOWCARD]'] } })
    render(<ReadPanel question={q} canTeach={false} onTeach={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Teach this' })).toBeNull()
  })
})
