import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { act } from 'react'
import type { CopilotProposal } from '../api/client'
import CopilotDrawer, { askCopilot, focusQuestion, studyFromPath } from './CopilotDrawer'

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

vi.mock('../api/client', () => {
  const ask = vi.fn()
  return {
  copilotApi: {
    ask,
    // The panel streams; these tests assert on the request it makes.
    askStreaming: vi.fn(async (m: string, onStep: (l: string) => void, s?: string | null,
      c?: string | null, f?: string | null) => {
      onStep('Reading the project')
      return (await ask(m, s, c, f)).data
    }),
    confirm: vi.fn(),
    conversations: vi.fn().mockResolvedValue({ data: [] }),
    memories: vi.fn().mockResolvedValue({ data: [{ id: 'm1', scope: 'user', client: null, text: 'Short bullets', created_by: 'A', created_at: '' }] }),
    forget: vi.fn().mockResolvedValue({}),
    conversation: vi.fn(),
    chart: vi.fn().mockResolvedValue({ data: new Blob(['png'], { type: 'image/png' }) }),
    excel: vi.fn().mockResolvedValue({ data: new Blob(['xlsx']) }),
  },
  errorMessage: (e: unknown) => (e instanceof Error ? e.message : null),
  }
})

async function ask(reply: string, proposals: CopilotProposal[] = [card]) {
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
  fireEvent.change(screen.getByPlaceholderText('Ask about any project, or paste a screenshot…'), {
    target: { value: 'hold it at 50' },
  })
  fireEvent.click(screen.getByText('Ask'))
  return copilotApi
}

describe('CopilotDrawer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('reopens the last conversation, and its old cards cannot be confirmed', async () => {
    const { copilotApi } = await import('../api/client')
    localStorage.setItem('dvv.copilot.conversation', 'conv_1')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(copilotApi.conversation as any).mockResolvedValue({
      data: {
        id: 'conv_1', title: 'hold it', updated_at: '2026-10-27',
        messages: [
          { role: 'user', content: 'hold it at 50', proposals: [], tools_used: [], created_at: 'x' },
          { role: 'assistant', content: 'Ready yesterday.', proposals: [card], tools_used: [],
            answered_by: 'Bedrock', created_at: 'x' },
        ],
      },
    })
    render(
      <MemoryRouter initialEntries={['/projects/s1/fielding']}>
        <CopilotDrawer open onClose={() => {}} />
      </MemoryRouter>,
    )
    await screen.findByText('Ready yesterday.')
    expect(screen.queryByText('Confirm')).toBeNull()
    expect(screen.getByText(/can no longer be confirmed/)).toBeTruthy()

    // A follow-up continues the same conversation on the server.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(copilotApi.ask as any).mockResolvedValue({
      data: { reply: 'Still held.', proposals: [], tools_used: [], answered_by: 'Bedrock',
              conversation_id: 'conv_1' },
    })
    fireEvent.change(screen.getByPlaceholderText('Ask about any project, or paste a screenshot…'), {
      target: { value: 'still?' },
    })
    fireEvent.click(screen.getByText('Ask'))
    await screen.findByText('Still held.')
    expect(copilotApi.ask).toHaveBeenCalledWith('still?', 's1', 'conv_1', null)
  })

  it('New chat starts over and forgets the conversation in this browser', async () => {
    await ask('Ready.', [])
    await screen.findByText('Ready.')
    fireEvent.click(screen.getByText('New chat'))
    expect(screen.queryByText('Ready.')).toBeNull()
    expect(localStorage.getItem('dvv.copilot.conversation')).toBeNull()
  })

  it('knows which project is in view, and ignores the new-project page', () => {
    expect(studyFromPath('/projects/s1/fielding')).toBe('s1')
    expect(studyFromPath('/projects/new')).toBeNull()
    expect(studyFromPath('/dashboard')).toBeNull()
  })

  it('sends the project in view as context', async () => {
    const api = await ask('Ready.')
    await screen.findByText('Ready.')
    expect(api.ask).toHaveBeenCalledWith('hold it at 50', 's1', null, null)
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

  it('renders a crosstab as a table, not as raw pipes', async () => {
    // A crossbreak IS the answer. It was arriving as "| Age band | Aware % |"
    // in the panel — the shape it is sent in, and not one anybody can read.
    await ask(
      'Awareness by age:\n\n' +
        '| Age band | Aware % |\n' +
        '|---|---|\n' +
        '| 18-34 | 68.6 |\n' +
        '| 35-54 | 55.0 |\n\n' +
        'Higher among 18-34s.',
    )
    const cell = await screen.findByText('18-34')
    expect(cell.closest('table')).not.toBeNull()
    expect(screen.getByText('Age band').tagName).toBe('TH')
    // The prose around it survives.
    expect(screen.getByText('Higher among 18-34s.')).toBeTruthy()
    // And nobody sees the markup.
    expect(screen.queryByText(/\|---\|/)).toBeNull()
  })

  it('leaves prose that merely contains a pipe alone', async () => {
    await ask('Use the a|b convention for that variable.')
    const line = await screen.findByText(/a\|b convention/)
    expect(line.closest('table')).toBeNull()
  })

  it('bars the percentage column, not the base beside it', async () => {
    // A crossbreak carries several numbers per row. The percentage is what it
    // is read on; an n and a base are context. Charting "Base" would draw a
    // picture of the sample rather than of the finding.
    await ask(
      '| Age band | Aware n | Aware % | Base |\n|---|---|---|---|\n' +
        '| 18-34 | 48 | 68.6 | 70 |\n| 55+ | 4 | 40.0 | 10 |',
    )
    await screen.findByText('18-34')
    const bars = document.querySelectorAll('span[aria-hidden]')
    expect(bars.length).toBe(2)
    // Widths follow 68.6 and 40.0, not 70 and 10.
    const widths = [...bars].map((b) => parseInt((b as HTMLElement).style.width, 10))
    expect(widths[0]).toBeGreaterThan(widths[1])
    expect(widths[1] / widths[0]).toBeCloseTo(40.0 / 68.6, 1)
  })

  it('draws no bars when several numbers compete and none is a percentage', async () => {
    // Guessing which of three numbers matters would chart the wrong thing.
    await ask('| Brand | Starts | Completes | Terminates |\n|---|---|---|---|\n| Alpha | 90 | 60 | 30 |')
    await screen.findByText('Alpha')
    expect(document.querySelectorAll('span[aria-hidden]').length).toBe(0)
  })

  it('sends the question open on the questionnaire page, only for that project', async () => {
    const { copilotApi } = await import('../api/client')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(copilotApi.ask as any).mockResolvedValue({
      data: { reply: 'It asks about coffee.', proposals: [], tools_used: [], answered_by: 'Bedrock' },
    })
    render(
      <MemoryRouter initialEntries={['/projects/s1/questionnaire']}>
        <CopilotDrawer open onClose={() => {}} />
      </MemoryRouter>,
    )
    act(() => focusQuestion('s1', 'S3'))
    act(() => askCopilot('About S3: '))
    const box = screen.getByPlaceholderText('Ask about any project, or paste a screenshot…') as HTMLInputElement
    expect(box.value).toBe('About S3: ')
    fireEvent.change(box, { target: { value: 'About S3: what does it ask?' } })
    fireEvent.click(screen.getByText('Ask'))
    await screen.findByText('It asks about coffee.')
    expect(copilotApi.ask).toHaveBeenCalledWith('About S3: what does it ask?', 's1', null, 'S3')

    act(() => focusQuestion('other', 'Q9')) // another project's question is not this one
    expect(screen.queryByText(/· Q9/)).toBeNull()
  })

  it('an edit card shows before and after', async () => {
    await ask('Ready to confirm.', [{
      ...card, kind: 'edit_question', title: 'Change Q2: options', method: 'PATCH' as const,
      diff: [{ field: 'options', before: '1=Yes; 2=No', after: '1=Daily; 2=Sometimes; 3=No' }],
    }])
    expect(await screen.findByText('1=Daily; 2=Sometimes; 3=No')).toBeTruthy()
    expect(screen.getByText('1=Yes; 2=No')).toBeTruthy()
  })

  it('shows a table exactly as the engine computed it, under the reply', async () => {
    const { copilotApi } = await import('../api/client')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(copilotApi.ask as any).mockResolvedValue({
      data: {
        reply: 'Men are more often very satisfied.', proposals: [], tools_used: ['ask_data'],
        answered_by: 'Bedrock',
        tables: [{ title: 'Q4', header: ['Q4', 'Man (A)', 'Woman (B)'],
                   rows: [['Very satisfied', '36.4% B', '18.2%']], significance_tested: true }],
      },
    })
    render(
      <MemoryRouter initialEntries={['/projects/s1/fielding']}>
        <CopilotDrawer open onClose={() => {}} />
      </MemoryRouter>,
    )
    fireEvent.change(screen.getByPlaceholderText('Ask about any project, or paste a screenshot…'), { target: { value: 'Q4 by gender' } })
    fireEvent.click(screen.getByText('Ask'))
    expect(await screen.findByText('36.4% B')).toBeTruthy()
    expect(screen.getByText(/letters mark significant differences/)).toBeTruthy()
  })

  it('draws a chart and offers the table as Excel and CSV', async () => {
    const { copilotApi } = await import('../api/client')
    URL.createObjectURL = vi.fn(() => 'blob:chart')
    URL.revokeObjectURL = vi.fn()
    const table = { title: 'Q4', header: ['Q4', 'Total'], rows: [['Very satisfied', '27.3%']] }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(copilotApi.ask as any).mockResolvedValue({
      data: {
        reply: 'Here it is.', proposals: [], tools_used: ['ask_data'], answered_by: 'Bedrock',
        tables: [table], charts: [{ study_id: 's1', filename: 'a.png', title: 'Q4 chart' }],
      },
    })
    render(
      <MemoryRouter initialEntries={['/projects/s1']}>
        <CopilotDrawer open onClose={() => {}} />
      </MemoryRouter>,
    )
    fireEvent.change(screen.getByPlaceholderText('Ask about any project, or paste a screenshot…'), { target: { value: 'chart Q4' } })
    fireEvent.click(screen.getByText('Ask'))
    expect(await screen.findByAltText('Q4 chart')).toBeTruthy()
    expect(copilotApi.chart).toHaveBeenCalledWith({ study_id: 's1', filename: 'a.png', title: 'Q4 chart' })
    fireEvent.click(screen.getByText('Excel'))
    await vi.waitFor(() => expect(copilotApi.excel).toHaveBeenCalledWith([table]))
    expect(screen.getByText('CSV')).toBeTruthy()
  })
})

describe('Copilot memory', () => {
  it('shows what it remembers and forgets in one click', async () => {
    const { copilotApi } = await import('../api/client')
    render(<MemoryRouter initialEntries={['/projects/s1']}><CopilotDrawer open onClose={() => {}} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'Memory' }))
    expect(await screen.findByText('Short bullets')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Forget: Short bullets' }))
    await waitFor(() => expect(copilotApi.forget).toHaveBeenCalledWith('m1'))
    await waitFor(() => expect(screen.queryByText('Short bullets')).toBeNull())
  })
})


describe('Copilot questions', () => {
  it('shows what it needs as options and sends the answers together', async () => {
    const { copilotApi } = await import('../api/client')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(copilotApi.ask as any).mockResolvedValueOnce({
      data: {
        reply: 'A report by gender, I think. Two choices first.', proposals: [], tools_used: ['excel_report'],
        answered_by: 'Bedrock', questions: [
          { id: 'rows', question: 'Which questions?', options: ['Every question', 'Only some'] },
          { id: 'columns', question: 'Which columns?', options: ['Total only', 'S1 (gender)'], multiple: true },
        ],
      },
    })
    render(<MemoryRouter initialEntries={['/projects/s1']}><CopilotDrawer open onClose={() => {}} /></MemoryRouter>)
    fireEvent.change(screen.getByPlaceholderText(/Ask about any project/), { target: { value: 'excel report' } })
    fireEvent.submit(screen.getByPlaceholderText(/Ask about any project/).closest('form')!)
    expect(await screen.findByText('Which questions?')).toBeTruthy()
    const send = screen.getByRole('button', { name: 'Send answers' }) as HTMLButtonElement
    expect(send.disabled).toBe(true)  // every question answered first
    fireEvent.click(screen.getByRole('button', { name: 'Every question' }))
    fireEvent.click(screen.getByRole('button', { name: 'Total only' }))
    fireEvent.click(screen.getByRole('button', { name: 'S1 (gender)' }))
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(copilotApi.ask as any).mockResolvedValueOnce({
      data: { reply: 'Built.', proposals: [], tools_used: [], answered_by: 'Bedrock' } })
    fireEvent.click(send)
    await waitFor(() => expect(copilotApi.ask).toHaveBeenLastCalledWith(
      'Which questions? Every question\nWhich columns? Total only; S1 (gender)', 's1', null, null))
  })
})

describe('screenshots in the chat', () => {
  beforeEach(() => {
    // jsdom draws nothing: an image "loads" at once and the canvas returns a fixed URL.
    Object.defineProperty(globalThis.Image.prototype, 'src', {
      configurable: true,
      set(this: HTMLImageElement) {
        Object.defineProperty(this, 'naturalWidth', { value: 2400, configurable: true })
        Object.defineProperty(this, 'naturalHeight', { value: 1200, configurable: true })
        setTimeout(() => this.onload?.(new Event('load')))
      },
    })
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ fillRect: vi.fn(), drawImage: vi.fn(), fillStyle: '' })) as never
    HTMLCanvasElement.prototype.toDataURL = vi.fn(() => 'data:image/jpeg;base64,AAAA')
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:x')
    globalThis.URL.revokeObjectURL = vi.fn()
  })

  it('a pasted screenshot is shown, can be removed, and is sent with the question', async () => {
    const { copilotApi } = await import('../api/client')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(copilotApi.ask as any).mockResolvedValue({ data: { reply: 'It shows the review page.', proposals: [] } })
    render(
      <MemoryRouter initialEntries={['/projects/s1/questionnaire']}>
        <CopilotDrawer open onClose={() => {}} />
      </MemoryRouter>,
    )
    const box = screen.getByPlaceholderText('Ask about any project, or paste a screenshot…')
    const shot = new File(['png'], 'shot.png', { type: 'image/png' })
    fireEvent.paste(box, { clipboardData: { files: [shot, shot] } })
    expect(await screen.findByAltText('Screenshot 2 to send')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Remove screenshot 2'))
    expect(screen.queryByAltText('Screenshot 2 to send')).toBeNull()

    fireEvent.click(screen.getByText('Ask'))  // no words typed: the screenshot alone is a question
    await waitFor(() => expect(copilotApi.askStreaming).toHaveBeenCalled())
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const call = (copilotApi.askStreaming as any).mock.calls.at(-1)
    expect(call[0]).toBe('What does this screenshot show?')
    expect(call[6]).toEqual(['data:image/jpeg;base64,AAAA'])
    expect(await screen.findByAltText('Screenshot 1')).toBeTruthy()  // in the sent message
  })

  it('folds what the screenshot showed under the words typed', async () => {
    const { copilotApi } = await import('../api/client')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(copilotApi.conversation as any).mockResolvedValue({ data: { messages: [
      { role: 'user', content: 'why is this wrong?\n\n[Screenshot 1, as read]\nS6b. How long…\n[End of screenshot 1]', proposals: [] },
    ] } })
    localStorage.setItem('dvv.copilot.conversation', 'conv1')
    render(
      <MemoryRouter initialEntries={['/projects/s1/questionnaire']}>
        <CopilotDrawer open onClose={() => {}} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('What the screenshot showed')).toBeTruthy()
    expect(screen.getByText(/why is this wrong\?/)).toBeTruthy()
  })
})
