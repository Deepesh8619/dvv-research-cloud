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
    for (const raw of page.elements ?? []) {
      // The answer above already says what the box is for: its words become the
      // placeholder, not a second question title.
      const el: Element = isWriteInBox(String(raw.name ?? ''))
        ? { ...raw, titleLocation: 'hidden', placeholder: raw.title }
        : raw
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

function answered(q: Question): boolean {
  if (q.isEmpty()) return false
  if (q.getType() !== 'matrix') return true
  // A grid is answered when every statement shown has an answer.
  const rows = (q as unknown as { visibleRows?: Array<{ name: unknown }> }).visibleRows ?? []
  const value = q.value as Record<string, unknown>
  return rows.every((r) => value[String(r.name)] != null)
}

/** Whether what is on screen has been answered. Advisory: see `.dvv-t-clean[data-ready]`. */
export function canContinue(survey: Model): boolean {
  const element = (survey as unknown as { currentSingleElement?: { questions?: Question[] } })
    .currentSingleElement
  const questions = element?.questions ?? (survey.currentSingleQuestion ? [survey.currentSingleQuestion] : [])
  return questions.every((q) => !q.isVisible || !q.isRequired || answered(q))
}

/** A piped answer stands out in its sentence: `{Q1}` is wrapped here, drawn by `pipedBold`. */
export function emphasisePipes<T>(json: T): T {
  const source = json as { pages?: Array<{ elements?: Element[] }> }
  if (!source.pages) return json
  const mark = (el: Element): Element => ({
    ...el,
    ...(typeof el.title === 'string'
      ? { title: el.title.replace(/\{[^{}\s]+\}/g, (m) => `**${m}**`) }
      : {}),
    ...(Array.isArray(el.elements) ? { elements: (el.elements as Element[]).map(mark) } : {}),
  })
  return {
    ...source,
    pages: source.pages.map((p) => ({ ...p, elements: (p.elements ?? []).map(mark) })),
  } as T
}

const escapeHtml = (text: string) =>
  text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)

/** `onTextMarkdown`: **answer** to bold. Everything is escaped: the answer may be typed text. */
export function pipedBold(_: unknown, options: { text: string; html?: string }): void {
  if (!options.text.includes('**')) return
  options.html = escapeHtml(options.text)
    .replace(/\*\*\s*\*\*/g, '')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
}
