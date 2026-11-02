import type { Model, Question } from 'survey-core'

/**
 * The "Other (specify)" box belongs on the screen of the answer that opens it.
 *
 * The compiler emits it as its own question (`<qid>r<code>oe`, the data column),
 * and the live survey shows one question per screen, so a respondent picked
 * "Other", saw nothing happen, and met the box only after pressing Next. SurveyJS
 * keeps a panel together on one screen, so the question and its boxes go in one.
 */
const BOX = /r\d+oe$/

export const isWriteInBox = (name: string): boolean => BOX.test(name)

type Element = { name?: string } & Record<string, unknown>

export function groupWriteIns<T>(json: T): T {
  const source = json as { pages?: Array<{ elements?: Element[] }> }
  if (!source.pages) return json
  const pages = source.pages.map((page) => {
    const out: Element[] = []
    for (const el of page.elements ?? []) {
      const last = out[out.length - 1]
      const name = String(el.name ?? '')
      if (!isWriteInBox(name) || !last) {
        out.push(el)
      } else if (last.type === 'panel' && last.holdsWriteIn) {
        ;(last.elements as Element[]).push(el)
      } else if (name.startsWith(`${last.name}r`)) {
        out[out.length - 1] = {
          type: 'panel',
          name: `${last.name}_with_write_in`,
          holdsWriteIn: true,
          elements: [last, el],
        }
      } else {
        out.push(el)
      }
    }
    return { ...page, elements: out }
  })
  return { ...source, pages } as T
}

/** The question on screen, including the one a write-in panel holds. */
export function onScreen(survey: Model): Question | null {
  if (survey.currentSingleQuestion) return survey.currentSingleQuestion
  const element = (survey as unknown as { currentSingleElement?: { questions?: Question[] } })
    .currentSingleElement
  return element?.questions?.[0] ?? null
}
