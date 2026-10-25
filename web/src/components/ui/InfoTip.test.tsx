import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => ({
  uiApi: { overrides: vi.fn().mockResolvedValue({ data: { overrides: {
    'help.drivers.what': 'Which ratings move the overall score most.' } } }) },
}))

import { HELP } from '../../help/help'
import { LabelProvider } from '../../labels/LabelContext'
import InfoTip from './InfoTip'

describe('InfoTip', () => {
  it('explains what, why and how on hover, focus or tap, and closes on Escape', () => {
    render(<InfoTip id="drivers" />)
    const button = screen.getByRole('button', { name: 'About Key drivers' })
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.mouseEnter(button)
    const tip = screen.getByRole('tooltip')
    expect(tip.textContent).toContain('What it is.')
    expect(tip.textContent).toContain('not gender or age')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.click(button)  // a tap on a phone
    expect(screen.getByRole('tooltip')).toBeTruthy()
  })

  it("shows a founder's rewording from Admin → Wording", async () => {
    localStorage.setItem('authToken', 't')
    render(<MemoryRouter><LabelProvider><InfoTip id="drivers" /></LabelProvider></MemoryRouter>)
    fireEvent.mouseEnter(screen.getByRole('button', { name: 'About Key drivers' }))
    expect(await screen.findByText('Which ratings move the overall score most.')).toBeTruthy()
    localStorage.removeItem('authToken')
  })

  it('every entry says what, why and how', () => {
    for (const [id, h] of Object.entries(HELP)) {
      expect(h.what.length > 20 && h.why.length > 20 && h.how.length > 20, id).toBe(true)
    }
  })
})
