import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import RoutingEditor, { FlatRouting } from './RoutingEditor'

/**
 * These type ONE CHARACTER AT A TIME on purpose.
 *
 * The bug being guarded was per-keystroke: the input's value was derived from
 * the parsed `values` array, so "1," parsed to [1] and re-rendered as "1",
 * erasing the separator the instant it was typed. A test that sets the whole
 * value at once (fireEvent.change) reproduces nothing — it never asks the
 * component to survive a half-typed state, which is exactly where it failed.
 */

/** Wraps the controlled editor so typing actually round-trips through state. */
function Harness({ initial }: { initial: FlatRouting }) {
  const [flat, setFlat] = useState<FlatRouting>(initial)
  return (
    <>
      <RoutingEditor
        label="Terminate / screen out if"
        flat={flat}
        onChange={setFlat}
        qidOptions={['Q1', 'Q2']}
      />
      <output data-testid="parsed">{JSON.stringify(flat.conditions[0]?.values ?? null)}</output>
      <button type="button">elsewhere</button>
    </>
  )
}

const oneInCondition: FlatRouting = {
  editable: true,
  connective: 'and',
  conditions: [{ op: 'in', qid: 'Q1', values: [] }],
  raw: null,
}

describe('codes box', () => {
  it('keeps every comma while a multi-code screen-out is typed', async () => {
    const user = userEvent.setup()
    render(<Harness initial={oneInCondition} />)

    const box = screen.getByLabelText('condition values') as HTMLInputElement
    await user.click(box)
    await user.keyboard('1,3,98')

    expect(box.value).toBe('1,3,98')
    expect(screen.getByTestId('parsed').textContent).toBe('[1,3,98]')
  })

  it('survives a half-typed separator', async () => {
    const user = userEvent.setup()
    render(<Harness initial={oneInCondition} />)

    const box = screen.getByLabelText('condition values') as HTMLInputElement
    await user.click(box)
    await user.keyboard('1,')

    // The exact state the old code could not hold: a trailing separator with
    // nothing after it yet.
    expect(box.value).toBe('1,')
  })

  it('normalises the display on blur so what is shown is what will be saved', async () => {
    const user = userEvent.setup()
    render(<Harness initial={oneInCondition} />)

    const box = screen.getByLabelText('condition values') as HTMLInputElement
    await user.click(box)
    await user.keyboard('1,,3 ,98')
    await user.click(screen.getByRole('button', { name: 'elsewhere' }))

    expect(box.value).toBe('1, 3, 98')
    expect(screen.getByTestId('parsed').textContent).toBe('[1,3,98]')
  })

  it('lets a code be deleted without losing the ones around it', async () => {
    const user = userEvent.setup()
    render(
      <Harness
        initial={{ ...oneInCondition, conditions: [{ op: 'in', qid: 'Q1', values: [1, 3, 98] }] }}
      />,
    )

    const box = screen.getByLabelText('condition values') as HTMLInputElement
    await user.click(box)
    await user.keyboard('{End}{Backspace}{Backspace}')

    expect(box.value).toBe('1, 3, ')
    expect(screen.getByTestId('parsed').textContent).toBe('[1,3]')
  })
})
