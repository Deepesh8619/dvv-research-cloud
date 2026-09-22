import { Model } from 'survey-core'
import { describe, expect, it } from 'vitest'

import { applyGridMaskVariables } from '../../pages/RespondentPage'

/**
 * A mask sourced from a grid, against a REAL SurveyJS model — because the
 * claim being tested is that SurveyJS can now evaluate the condition, and
 * only SurveyJS can settle that.
 *
 * Before this, the condition was `{Q5} = {item}`: a {row: column} object
 * compared to an option code, never true, so the masked question offered the
 * respondent no answers at all.
 */
function pharmacyStudy() {
  const model = new Model({
    elements: [
      {
        type: 'matrix',
        name: 'Q5',
        title: 'Which of these pharmacies have you heard of?',
        rows: [
          { value: 1, text: 'CVS' },
          { value: 2, text: 'Walgreens' },
        ],
        columns: [
          { value: 1, text: 'Yes' },
          { value: 2, text: 'No' },
        ],
      },
      {
        type: 'ranking',
        name: 'Q6',
        title: 'Rank your top 3',
        choices: [
          { value: 1, text: 'CVS' },
          { value: 2, text: 'Walgreens' },
        ],
        choicesVisibleIf: '{Q5_picked} contains {item}',
      },
    ],
  })
  return { model, sources: [{ name: 'Q5_picked', qid: 'Q5', positive: [1] }] }
}

function offered(model: Model): string[] {
  const question = model.getQuestionByName('Q6') as unknown as {
    visibleChoices: Array<{ text: string }>
  }
  return question.visibleChoices.map((c) => c.text)
}

describe('a mask whose source is a grid', () => {
  it('offers only the rows the respondent answered Yes to', () => {
    const { model, sources } = pharmacyStudy()
    model.getQuestionByName('Q5')!.value = { 1: 1, 2: 2 } // Yes to CVS, No to Walgreens
    applyGridMaskVariables(model, sources)
    expect(offered(model)).toEqual(['CVS'])
  })

  it('offers both when both were answered Yes', () => {
    const { model, sources } = pharmacyStudy()
    model.getQuestionByName('Q5')!.value = { 1: 1, 2: 1 }
    applyGridMaskVariables(model, sources)
    expect(offered(model)).toEqual(['CVS', 'Walgreens'])
  })

  it('offers nothing when they had heard of none — which is the honest answer', () => {
    const { model, sources } = pharmacyStudy()
    model.getQuestionByName('Q5')!.value = { 1: 2, 2: 2 }
    applyGridMaskVariables(model, sources)
    expect(offered(model)).toEqual([])
  })

  it('does not throw before the grid has been answered at all', () => {
    const { model, sources } = pharmacyStudy()
    expect(() => applyGridMaskVariables(model, sources)).not.toThrow()
    expect(offered(model)).toEqual([])
  })
})
