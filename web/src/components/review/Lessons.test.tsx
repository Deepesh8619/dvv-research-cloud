import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ProfileRule } from '../../api/client'
import Lessons from './Lessons'

vi.mock('../../api/client', () => ({
  correctionsApi: { rules: vi.fn(), toggleRule: vi.fn() },
}))

async function api() {
  return (await import('../../api/client')) as unknown as {
    correctionsApi: {
      rules: ReturnType<typeof vi.fn>
      toggleRule: ReturnType<typeof vi.fn>
    }
  }
}

afterEach(() => vi.clearAllMocks())

const rule = (over: Partial<ProfileRule> = {}): ProfileRule => ({
  id: 'r1',
  profile_slug: 'acme',
  rule_text: '[SHOWCARD] means a showcard is displayed',
  pattern_kind: 'type_signal',
  pattern: '[SHOWCARD]',
  pattern_value: 'single',
  scope: 'profile',
  ordinal: 0,
  active: true,
  approved_by: null,
  approved_at: null,
  created_at: null,
  ...over,
})

describe('Lessons', () => {
  it('lists the active patterns for this profile, once opened', async () => {
    const { correctionsApi } = await api()
    correctionsApi.rules.mockResolvedValue({ data: { profile_slug: 'acme', rules: [rule()] } })
    render(<Lessons profileSlug="acme" profileLabel="Acme" canCorrect />)

    fireEvent.click(screen.getByRole('button', { name: /Lessons this format has learned/ }))
    await waitFor(() => expect(screen.getByText(/\[SHOWCARD\]/)).toBeTruthy())
    expect(screen.getByText(/type signal/)).toBeTruthy()
  })

  it('hides the on/off toggle for a non-founder, showing the state read-only', async () => {
    const { correctionsApi } = await api()
    correctionsApi.rules.mockResolvedValue({ data: { profile_slug: 'acme', rules: [rule()] } })
    render(<Lessons profileSlug="acme" canCorrect={false} />)

    fireEvent.click(screen.getByRole('button', { name: /Lessons this format has learned/ }))
    await waitFor(() => expect(screen.getByText(/\[SHOWCARD\]/)).toBeTruthy())
    expect(screen.queryByTitle(/Switch this lesson/)).toBeNull()
  })

  it('toggles a lesson off for a founder', async () => {
    const { correctionsApi } = await api()
    correctionsApi.rules.mockResolvedValue({ data: { profile_slug: 'acme', rules: [rule()] } })
    correctionsApi.toggleRule.mockResolvedValue({ data: rule({ active: false }) })
    render(<Lessons profileSlug="acme" canCorrect />)

    fireEvent.click(screen.getByRole('button', { name: /Lessons this format has learned/ }))
    await waitFor(() => expect(screen.getByText(/\[SHOWCARD\]/)).toBeTruthy())
    fireEvent.click(screen.getByTitle('Switch this lesson off'))
    await waitFor(() => expect(correctionsApi.toggleRule).toHaveBeenCalledWith('acme', 'r1'))
  })
})
