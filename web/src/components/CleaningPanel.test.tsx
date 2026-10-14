import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CleaningPanel from './CleaningPanel'

vi.mock('../api/client', () => ({
  cleaningApi: { get: vi.fn(), rules: vi.fn() },
}))

const state = (unreviewed: number) => ({
  data: {
    key_column: 'uuid',
    flags: {
      a1: [{ check: 'speeder', detail: 'finished in 40s' }],
      b2: [{ check: 'straightliner', detail: 'same answer 12x' }, { check: 'gibberish', detail: 'asdf' }],
    },
    skipped: [], decisions: unreviewed ? {} : { a1: { decision: 'keep' }, b2: { decision: 'remove' } },
    n_unreviewed_flagged: unreviewed, locked: false, versions: [], rules: [], imputations: [],
  },
})

async function setup(unreviewed: number) {
  const { cleaningApi } = await import('../api/client')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const api = cleaningApi as any
  api.get.mockResolvedValue(state(unreviewed))
  api.rules.mockResolvedValue({ data: { rules: [], proposed: [] } })
}

describe('CleaningPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders flags with check details, most flags first', async () => {
    await setup(2)
    render(<CleaningPanel studyId="s" role="analyst" />)
    expect(await screen.findByText(/finished in 40s/)).toBeTruthy()
    expect(screen.getByText(/same answer 12x/)).toBeTruthy()
    const ids = screen.getAllByText(/^(a1|b2)$/).map((e) => e.textContent)
    expect(ids).toEqual(['b2', 'a1'])
  })

  it('has no bulk-keep control', async () => {
    await setup(2)
    render(<CleaningPanel studyId="s" role="owner" />)
    await screen.findByText(/finished in 40s/)
    expect(screen.queryByText(/keep all|keep selected|bulk keep/i)).toBeNull()
    // Keep exists only per row (2 rows -> 2 buttons).
    expect(screen.getAllByRole('button', { name: 'Keep' })).toHaveLength(2)
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

  it('shows a reviewer no action buttons', async () => {
    await setup(2)
    render(<CleaningPanel studyId="s" role="reviewer" />)
    await screen.findByText(/finished in 40s/)
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })
})
