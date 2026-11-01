import { Model } from 'survey-core'
import { describe, expect, it } from 'vitest'
import { applyKeepTogether, orderWithGroups } from './keepTogether'

const list = (n: number) => Array.from({ length: n }, (_, i) => ({ value: i + 1 }))
const vals = (cs: Array<{ value: unknown }>) => cs.map((c) => c.value as number)
const adjacent = (order: number[], codes: number[]) => {
  const at = codes.map((c) => order.indexOf(c)).sort((a, b) => a - b)
  return at[at.length - 1] - at[0] === codes.length - 1
}

describe('orderWithGroups', () => {
  it('keeps every group adjacent, anchors in place, and is a permutation', () => {
    const groups = [{ codes: [3, 4, 5], shuffle_within: true }, { codes: [6, 7], shuffle_within: true }]
    const seen = new Set<string>()
    for (let i = 0; i < 500; i++) {
      const out = vals(orderWithGroups(list(12), groups, Math.random, [12]))
      expect([...out].sort((a, b) => a - b)).toEqual(list(12).map((c) => c.value))
      expect(adjacent(out, [3, 4, 5])).toBe(true)
      expect(adjacent(out, [6, 7])).toBe(true)
      expect(out[11]).toBe(12)
      seen.add(out.join())
    }
    expect(seen.size).toBeGreaterThan(50)
  })

  it('keeps the given order inside a fixed-within group, and shuffles an unfixed one', () => {
    const fixed = new Set<string>()
    const loose = new Set<string>()
    for (let i = 0; i < 300; i++) {
      const a = vals(orderWithGroups(list(8), [{ codes: [2, 3, 4], shuffle_within: false }], Math.random))
      const at = a.indexOf(2)
      expect(a.slice(at, at + 3)).toEqual([2, 3, 4])
      fixed.add(a.join())
      const b = vals(orderWithGroups(list(8), [{ codes: [2, 3, 4], shuffle_within: true }], Math.random))
      const bt = Math.min(...[2, 3, 4].map((c) => b.indexOf(c)))
      loose.add(b.slice(bt, bt + 3).join())
    }
    expect(fixed.size).toBeGreaterThan(1)
    expect(loose.size).toBe(6)
  })

  it('moves a group that is the whole list, and leaves one choice alone', () => {
    expect(vals(orderWithGroups(list(1), [], Math.random))).toEqual([1])
    const out = vals(orderWithGroups(list(2), [{ codes: [1, 2] }], Math.random))
    expect(out.sort()).toEqual([1, 2])
  })

  it('applies to a model once, so the order is the same on every page', () => {
    const json = { pages: [{ elements: [{ type: 'radiogroup', name: 'Q1', choices: list(6), keepTogether: [{ codes: [2, 3], shuffle_within: true }] }] }] }
    const m = new Model(json)
    applyKeepTogether(m, json)
    const first = vals((m.getQuestionByName('Q1') as unknown as { choices: Array<{ value: number }> }).choices)
    expect(adjacent(first, [2, 3])).toBe(true)
    m.currentPageNo = 0
    expect(vals((m.getQuestionByName('Q1') as unknown as { choices: Array<{ value: number }> }).choices)).toEqual(first)
  })
})
