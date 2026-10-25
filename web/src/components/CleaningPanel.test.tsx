import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CleaningPanel from './CleaningPanel'

vi.mock('../api/client', () => ({
  cleaningApi: { get: vi.fn(), rules: vi.fn(), evidence: vi.fn(), decide: vi.fn() },
}))

const state = (unreviewed: number, extra: Record<string, unknown> = {}) => ({
  data: {
    key_column: 'uuid',
    flags: {
      a1: [{ check: 'speeder', detail: 'short interview (qtime)' }],
      b2: [{ check: 'gibberish', detail: 'Q5' }, { check: 'gibberish', detail: 'Q7a' },
           { check: 'straightliner', detail: 'same answer across a grid' }],
    },
    skipped: [], decisions: unreviewed ? {} : { a1: { decision: 'keep' }, b2: { decision: 'remove' } },
    n_unreviewed_flagged: unreviewed, locked: false, versions: [], rules: [], imputations: [],
    ...extra,
  },
})

async function setup(unreviewed: number, extra: Record<string, unknown> = {}) {
  const { cleaningApi } = await import('../api/client')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const api = cleaningApi as any
  api.get.mockResolvedValue(state(unreviewed, extra))
  api.rules.mockResolvedValue({ data: { rules: [], proposed: [] } })
  api.decide.mockResolvedValue({ data: {} })
  api.evidence.mockImplementation((_s: string, key: string) => Promise.resolve({ data: key === 'b2'
    ? { seconds: 300, median_seconds: 720, items: [
        { check: 'gibberish', detail: 'Q5', answers: [{ column: 'Q5', question: 'Why did you choose it?', answer: 'asdkjh qwe' }] },
        { check: 'gibberish', detail: 'Q7a', answers: [{ column: 'Q7a', question: 'Anything else?', answer: 'fgfgfg' }] },
        { check: 'straightliner', detail: '', answers: [{ column: 'Q12r1', question: 'Rate each', answer: 'all 7 rows answered “Agree”' }] }] }
    : { seconds: 120, median_seconds: 720, items: [{ check: 'speeder', detail: 'short interview (qtime)', answers: [] }] } }))
  return api
}

describe('CleaningPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lists respondents with flags grouped, worst first, and shows the answers behind them', async () => {
    await setup(2)
    render(<CleaningPanel studyId="s" role="analyst" />)
    expect(await screen.findByText('Gibberish ×2, Straight-lined')).toBeTruthy()
    expect(await screen.findByText('asdkjh qwe')).toBeTruthy()  // the evidence, not just "gibberish: Q5"
    expect(screen.getByText(/all 7 rows answered/)).toBeTruthy()
    expect(screen.getByText('Took 5 min · most took 12 min')).toBeTruthy()
  })

  it('shows the same concern score as the list, its reasons, and the pasted question in words', async () => {
    const api = await setup(2)
    const base = api.evidence.getMockImplementation()
    api.evidence.mockImplementation(async (s: string, k: string, f: unknown) => {
      const r = await base(s, k, f)
      return { data: { ...r.data, paradata: { pasted: ['Q15'], speech: [], max_chars_per_key: 40 },
        authenticity: { score: 65, band: 'review first', signals: [{ key: 'pasted_answer', label: 'Pasted answer', weight: 35, detail: 'Pasted rather than typed: Q15' }, { key: 'too_fast_to_read', label: 'Faster than reading', weight: 30, detail: 'finished in 40s' }] } } }
    })
    render(<CleaningPanel studyId="s" role="analyst" />)
    expect(await screen.findByText('Concern 65 / 100')).toBeTruthy()
    expect(screen.getAllByText('review first').length).toBeGreaterThan(0)
    expect(screen.getByText('Pasted answer')).toBeTruthy()
    expect(screen.getByText('⚠ Pasted: Q15')).toBeTruthy()
  })

  it('pre-fills the reason from the strongest flag and decides with K, then moves on', async () => {
    const api = await setup(2)
    render(<CleaningPanel studyId="s" role="analyst" />)
    await screen.findByText('asdkjh qwe')
    expect((screen.getByLabelText('reason for b2') as HTMLSelectElement).value).toBe('gibberish')
    fireEvent.keyDown(screen.getByLabelText(/arrow keys move/), { key: 'r' })
    expect(api.decide).toHaveBeenCalledWith('s', { keys: ['b2'], decision: 'remove', reason_code: 'gibberish', note: undefined })
  })

  it('needs a note for "other" and has no bulk keep', async () => {
    await setup(2)
    render(<CleaningPanel studyId="s" role="owner" />)
    await screen.findByText('asdkjh qwe')
    fireEvent.change(screen.getByLabelText('reason for b2'), { target: { value: 'other' } })
    expect((screen.getByRole('button', { name: 'Keep' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByText(/keep all|keep selected|bulk keep/i)).toBeNull()
    expect(screen.getAllByRole('button', { name: 'Keep' })).toHaveLength(1)
  })

  it('filters by check and hides reviewed respondents unless asked', async () => {
    await setup(0)
    render(<CleaningPanel studyId="s" role="analyst" />)
    expect(await screen.findByText(/Nobody here/)).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Show reviewed'))
    fireEvent.click(screen.getByRole('button', { name: 'Too fast 1' }))
    const list = screen.getByRole('list', { name: 'Flagged respondents' })
    expect(within(list).getByText('a1')).toBeTruthy()
    expect(within(list).queryByText('b2')).toBeNull()
  })

  it('offers removing every "review first" respondent together', async () => {
    const api = await setup(2, { authenticity: {
      a1: { score: 70, band: 'review first', reasons: [] }, b2: { score: 65, band: 'review first', reasons: [] } } })
    render(<CleaningPanel studyId="s" role="analyst" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Review first' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove all 2' }))
    expect(api.decide.mock.calls[0][1]).toMatchObject({ keys: ['a1', 'b2'], decision: 'remove', reason_code: 'other' })
  })

  it('disables Lock while unreviewed > 0 and enables it at 0', async () => {
    await setup(2)
    const { unmount } = render(<CleaningPanel studyId="s" role="analyst" />)
    const locked = (await screen.findByRole('button', { name: /Lock cleaning \(2 unreviewed\)/ })) as HTMLButtonElement
    expect(locked.disabled).toBe(true)
    unmount()
    await setup(0)
    render(<CleaningPanel studyId="s" role="analyst" />)
    const ok = (await screen.findByRole('button', { name: 'Lock cleaning' })) as HTMLButtonElement
    expect(ok.disabled).toBe(false)
  })

  it('shows a reviewer the evidence but no decision buttons', async () => {
    await setup(2)
    render(<CleaningPanel studyId="s" role="reviewer" />)
    await screen.findByText('asdkjh qwe')
    expect(screen.queryByRole('button', { name: 'Keep' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull()
  })
})
