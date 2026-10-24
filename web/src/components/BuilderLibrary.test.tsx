import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ModulePicker, TemplatePicker } from './BuilderLibrary'

const nps = { id: 'nps', title: 'Net Promoter Score', description: '0–10 and why.',
  inputs: [{ key: 'subject', label: 'Brand or company being rated', kind: 'text' as const, placeholder: 'Nescafé' }] }

describe('builder library', () => {
  it('offers blank or a template as cards', () => {
    const onChoose = vi.fn()
    render(<TemplatePicker templates={[{ ...nps, id: 'csat', title: 'Customer satisfaction' }]} chosen={null} onChoose={onChoose} />)
    expect(screen.getByRole('radio', { name: /Blank/ }).getAttribute('aria-checked')).toBe('true')
    fireEvent.click(screen.getByRole('radio', { name: /Customer satisfaction/ }))
    expect(onChoose).toHaveBeenCalledWith('csat')
  })

  it('opens a block, asks only its inputs, and adds it', async () => {
    const onAdd = vi.fn().mockResolvedValue(null)
    render(<ModulePicker modules={[nps]} onAdd={onAdd} />)
    fireEvent.click(screen.getByText('Add a block (1)'))
    fireEvent.click(screen.getByRole('button', { name: /Net Promoter Score/ }))
    fireEvent.change(screen.getByLabelText('Brand or company being rated'), { target: { value: 'Bru' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add block' }))
    expect(onAdd).toHaveBeenCalledWith('nps', { subject: 'Bru' })
    expect(await screen.findByRole('button', { name: /Net Promoter Score/ })).toBeTruthy()  // closed again
  })
})
