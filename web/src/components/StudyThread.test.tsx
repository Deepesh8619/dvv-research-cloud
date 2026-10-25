import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  studiesApi: {
    cellProvenance: vi.fn().mockResolvedValue({ data: {
      value: 64, label: 'Price', column: 'Total',
      steps: [{ title: 'The cell', detail: 'Price among Total: 64%.' },
              { title: 'The base', detail: 'No table filter.' },
              { title: 'Weight', detail: 'Weighted by wt.' }],
    } }),
  },
}))

import StudyThread from './StudyThread'

describe('StudyThread', () => {
  it('draws one knot per step, in order', async () => {
    render(<StudyThread studyId="s1" tableId="t1" row={0} col={0} onClose={() => {}} />)
    const knots = await screen.findAllByTestId('knot')
    expect(knots.map((k) => k.querySelector('b')?.textContent)).toEqual(['The cell', 'The base', 'Weight'])
    expect(screen.getByText('Why 64.0%')).toBeTruthy()
  })

  it('closes on Escape and on the close button', async () => {
    const onClose = vi.fn()
    render(<StudyThread studyId="s1" tableId="t1" row={0} col={0} onClose={onClose} />)
    await screen.findAllByTestId('knot')
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(screen.getByText('Close'))
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
