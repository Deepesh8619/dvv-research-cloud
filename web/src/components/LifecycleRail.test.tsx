import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

const stages = vi.hoisted(() => ['Brief', 'Questionnaire', 'Proof', 'Field', 'Verify', 'Data', 'Tables', 'Insight'].map((label, i) => ({
  key: label.toLowerCase(), label, detail: 'd', tab: i === 4 ? 'Cleaning' : 'Overview',
  state: i === 0 ? 'done' : i === 4 ? 'attention' : 'todo',
})))

vi.mock('../api/client', () => ({
  studiesApi: {
    lifecycle: vi.fn().mockResolvedValue({
      data: { stages, needs_you: [{ kind: 'verify', title: 'Verify needs you', detail: 'd', tab: 'Cleaning' }] },
    }),
  },
}))

import LifecycleRail from './LifecycleRail'

describe('LifecycleRail', () => {
  it('shows each state as text and opens the stage tab on click', async () => {
    const onOpen = vi.fn()
    render(<LifecycleRail studyId="s1" onOpen={onOpen} />)
    const verify = await screen.findByRole('button', { name: /Verify/ })
    expect(screen.getByRole('button', { name: /Brief/ }).textContent).toContain('✓')
    expect(verify.textContent).toContain('needs you')
    expect(screen.getAllByRole('listitem').length).toBeGreaterThanOrEqual(9)
    fireEvent.click(verify)
    expect(onOpen).toHaveBeenCalledWith('Cleaning')
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    expect(onOpen).toHaveBeenCalledTimes(2)
  })
})
