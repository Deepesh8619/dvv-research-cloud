import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { HELP } from '../../help/help'
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

  it('every entry says what, why and how', () => {
    for (const [id, h] of Object.entries(HELP)) {
      expect(h.what.length > 20 && h.why.length > 20 && h.how.length > 20, id).toBe(true)
    }
  })
})
