import { FunctionFactory, type Model } from 'survey-core'

/**
 * The follow-up after a thin open answer. `isThin` is the server's rule
 * (src/engines/fielding/probes.py) — the two are tested on the same answers.
 */
const VAGUE =
  /^\W*(good|bad|ok(ay)?|fine|nice|great|nothing( (else|comes to mind))?|none|no|yes|nope|na|n\/?a|nil|idk|i don'?t know|don'?t know|dont know|not sure|no idea|same|no comment|-+|\.+)\W*$/i
export const MIN_WORDS = 6
export const PROBE = '_PROBE'

export function isThin(answer: unknown): boolean {
  const text = typeof answer === 'string' ? answer.trim() : ''
  if (!text) return false
  return VAGUE.test(text) || (text.match(/[\p{L}]{2,}/gu) ?? []).length < MIN_WORDS
}

FunctionFactory.Instance.register('isThin', (params: unknown[]) => isThin(params[0]))

/** When a follow-up page opens, swap the fixed wording for the one written for this answer. */
export async function askFollowUp(
  survey: Model,
  fetchProbe: (qid: string, answer: string) => Promise<string | null>,
): Promise<void> {
  // One question per page swaps questions inside a single page: the one on
  // screen is currentSingleQuestion (onCurrentPageChanged still fires per move).
  const question = survey.currentSingleQuestion ?? survey.currentPage?.questions[0]
  if (!question?.name.endsWith(PROBE)) return
  const qid = question.name.slice(0, -PROBE.length)
  const answer = survey.getValue(qid)
  if (typeof answer !== 'string') return
  try {
    const text = await fetchProbe(qid, answer)
    if (text) question.title = text
  } catch {
    /* the fixed follow-up already on the page still asks */
  }
}
