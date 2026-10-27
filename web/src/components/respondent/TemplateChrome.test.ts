import { describe, expect, it } from 'vitest'
import type { ItemValue } from 'survey-core'
import { answerText } from './TemplateChrome'
import { isFeelingScale } from './controls'

describe('answerText', () => {
  it('reads every answer shape as one line', () => {
    expect(answerText('Every day')).toBe('Every day')
    expect(answerText(['Taste', 'Price'])).toBe('Taste, Price')
    expect(answerText({ Taste: 'Very', Price: ['1', '2'] })).toBe('Taste: Very · Price: 1, 2')
    expect(answerText(null)).toBe('')
  })
})

describe('isFeelingScale', () => {
  const cols = (...texts: string[]) => texts.map((text, i) => ({ value: i + 1, text })) as unknown as ItemValue[]
  it('gives faces to a satisfaction scale, never to a list of brands', () => {
    expect(isFeelingScale(cols('Very dissatisfied', 'Neither', 'Very satisfied'))).toBe(true)
    expect(isFeelingScale(cols('Bru', 'Nescafé', 'Davidoff', 'Sunrise', 'None'))).toBe(false)
    expect(isFeelingScale(cols('Agree', 'Disagree'))).toBe(false)
  })
})
