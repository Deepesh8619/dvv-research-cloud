import { Model } from 'survey-core'
import { describe, expect, it, vi } from 'vitest'

import { askFollowUp, isThin } from './probes'

// The same answers are in tests/unit/test_probes.py: the two rules must agree.
const THIN = ['Lower the rates', 'Nothing comes to mind', 'efficacy and safety', 'ok', "Don't know", 'N/A', 'good']
const FULL = ['The rates went up twice this year and the leads did not follow, so we cut spend', '', '   ']

describe('follow-up probes', () => {
  it('agrees with the server on what is thin', () => {
    for (const a of THIN) expect(isThin(a), a).toBe(true)
    for (const a of FULL) expect(isThin(a), a).toBe(false)
  })

  it('shows the follow-up page only for a thin answer and swaps in the written probe', async () => {
    const survey = new Model({ questionsOnPageMode: 'questionPerPage', elements: [
      { type: 'comment', name: 'Q5', title: 'Why?' },
      { type: 'comment', name: 'Q5_PROBE', title: 'Could you tell us more?', visibleIf: 'isThin({Q5})' },
    ] })
    survey.setValue('Q5', 'The rates went up twice this year and the leads did not follow')
    expect(survey.getQuestionByName('Q5_PROBE').isVisible).toBe(false)
    survey.setValue('Q5', 'Lower the rates')
    expect(survey.getQuestionByName('Q5_PROBE').isVisible).toBe(true)
    survey.currentSingleQuestion = survey.getQuestionByName('Q5_PROBE')
    const fetchProbe = vi.fn().mockResolvedValue('What would make the rates feel fair?')
    await askFollowUp(survey, fetchProbe)
    expect(fetchProbe).toHaveBeenCalledWith('Q5', 'Lower the rates')
    expect(survey.getQuestionByName('Q5_PROBE').title).toBe('What would make the rates feel fair?')
  })
})
