import { Model } from 'survey-core'
import { describe, expect, it } from 'vitest'

describe('awareness mask', () => {
  it('offers only the brands ticked earlier, plus None of these', () => {
    const m = new Model({ elements: [
      { type: 'checkbox', name: 'AWR_AIDED', choices: [1, 2, 3, 98].map((v) => ({ value: v, text: `B${v}` })) },
      { type: 'checkbox', name: 'AWR_CONSIDER', choices: [1, 2, 3, 98].map((v) => ({ value: v, text: `B${v}` })),
        choicesVisibleIf: '({AWR_AIDED} contains {item}) or {item} anyof [98]' },
    ] })
    m.setValue('AWR_AIDED', [1, 3])
    const visible = (m.getQuestionByName('AWR_CONSIDER') as unknown as { visibleChoices: Array<{ value: number }> })
      .visibleChoices.map((c) => c.value)
    expect(visible).toEqual([1, 3, 98])
  })
})
