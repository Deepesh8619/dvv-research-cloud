import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CopilotDrawer, { studyFromPath } from './CopilotDrawer'

/**
 * Nothing changes until a person confirms — and then only the request the
 * server built. Model text is shown as text: it can quote questionnaire content.
 */
const card = {
  id: 'c1',
  kind: 'hold',
  title: 'Hold link flink_1 at 50 completes',
  detail: '3 completes so far.',
  method: 'PATCH' as const,
  path: '/studies/s1/fielding/links/flink_1/soft-launch',
  body: { limit: 50 },
}

vi.mock('../api/client', () => ({
  copilotApi: { ask: vi.fn(), confirm: vi.fn() },
  errorMessage: (e: unknown) => (e instanceof Error ? e.message : null),
}))

async function ask(reply: string, proposals = [card]) {
  const { copilotApi } = await import('../api/client')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(copilotApi.ask as any).mockResolvedValue({
    data: { reply, proposals, tools_used: [], answered_by: 'Groq' },
  })
  render(
    <MemoryRouter initialEntries={['/projects/s1/fielding']}>
      <CopilotDrawer open onClose={() => {}} />
    </MemoryRouter>,
  )
  fireEvent.change(screen.getByPlaceholderText('Ask about any project…'), {
    target: { value: 'hold it at 50' },
  })
  fireEvent.click(screen.getByText('Ask'))
  return copilotApi
}

describe('CopilotDrawer', () => {
  beforeEach(() => vi.clearAllMocks())

  it('knows which project is in view, and ignores the new-project page', () => {
    expect(studyFromPath('/projects/s1/fielding')).toBe('s1')
    expect(studyFromPath('/projects/new')).toBeNull()
    expect(studyFromPath('/dashboard')).toBeNull()
  })

  it('sends the project in view as context', async () => {
    const api = await ask('Ready.')
    await screen.findByText('Ready.')
    expect(api.ask).toHaveBeenCalledWith('hold it at 50', [], 's1')
  })

  it('confirming sends exactly the server-built request, then announces the change', async () => {
    const api = await ask('Ready to confirm.')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(api.confirm as any).mockResolvedValue({ data: {} })
    const changed = vi.fn()
    window.addEventListener('dvv:changed', changed)

    fireEvent.click(await screen.findByText('Confirm'))
    await screen.findByText('Done.')
    expect(api.confirm).toHaveBeenCalledWith(card)
    expect(changed).toHaveBeenCalledTimes(1)
    window.removeEventListener('dvv:changed', changed)
  })

  it('dismissing changes nothing', async () => {
    const api = await ask('Ready to confirm.')
    fireEvent.click(await screen.findByText('Dismiss'))
    expect(screen.getByText('Dismissed — nothing changed.')).toBeTruthy()
    expect(api.confirm).not.toHaveBeenCalled()
  })

  it('a refused action says why, and nothing claims success', async () => {
    const api = await ask('Ready to confirm.')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(api.confirm as any).mockRejectedValue(new Error('This action requires one of these roles'))
    fireEvent.click(await screen.findByText('Confirm'))
    await screen.findByText('This action requires one of these roles')
    expect(screen.queryByText('Done.')).toBeNull()
  })

  it('shows model text as text — never as HTML', async () => {
    const { container } = render(<div />)
    await ask('Q1 says <img src=x onerror="alert(1)"> and **bold** stays safe', [])
    await waitFor(() => expect(screen.getByText(/onerror/)).toBeTruthy())
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(container).toBeTruthy()
  })
})
