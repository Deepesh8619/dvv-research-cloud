import { Model } from 'survey-core'
import { describe, expect, it } from 'vitest'

import { applyPipeVariables } from './RespondentPage'

/** A ranking's top choice, piped into a later question's wording. */
describe('pipe variables', () => {
  const model = () =>
    new Model({
      elements: [
        { type: 'ranking', name: 'Q7', choices: [
          { value: 1, text: 'Bubble' }, { value: 5, text: 'CeraVe' }, { value: 9, text: 'Glossier' }] },
        { type: 'comment', name: 'Q7a', title: 'You chose {Q7_rank1}, not {Q7_ranklast}.' },
      ],
    })
  const vars = [
    { name: 'Q7_rank1', qid: 'Q7', position: 1 },
    { name: 'Q7_ranklast', qid: 'Q7', position: -1 },
  ]

  it('fills the place in the ranking with the label the respondent read', () => {
    const survey = model()
    survey.setValue('Q7', [9, 1, 5])
    applyPipeVariables(survey, vars)
    expect(survey.getQuestionByName('Q7a').locTitle.renderedHtml).toBe('You chose Glossier, not CeraVe.')
  })

  it('leaves the sentence readable before they have ranked anything', () => {
    const survey = model()
    applyPipeVariables(survey, vars)
    expect(survey.getQuestionByName('Q7a').locTitle.renderedHtml).toBe('You chose , not .')
  })
})
