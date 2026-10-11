import { describe, expect, it } from 'vitest'
import { removeAndRenumber } from './optionCodes'

const opts = (...codes: number[]) => codes.map((code) => ({ code, label: `L${code}` }))
const codes = (o: { code: number }[]) => o.map((x) => x.code)

describe('removing an answer', () => {
  it('closes the gap in its run of codes', () => {
    expect(codes(removeAndRenumber(opts(1, 2, 3, 4), 1, false))).toEqual([1, 2, 3])
  })
  it('leaves system codes outside the run alone', () => {
    expect(codes(removeAndRenumber(opts(1, 2, 3, 98, 99), 0, false))).toEqual([1, 2, 98, 99])
  })
  it('keeps a scale that starts at 0 starting at 0', () => {
    expect(codes(removeAndRenumber(opts(0, 1, 2, 3), 2, false))).toEqual([0, 1, 2])
  })
  it('never moves a code the reviewer typed', () => {
    expect(codes(removeAndRenumber(opts(1, 2, 3, 4), 1, true))).toEqual([1, 3, 4])
  })
  it('keeps every other field of the answers it renumbers', () => {
    const out = removeAndRenumber([{ code: 1, label: 'A' }, { code: 2, label: 'None', exclusive: true }], 0, false)
    expect(out).toEqual([{ code: 1, label: 'None', exclusive: true }])
  })
})
