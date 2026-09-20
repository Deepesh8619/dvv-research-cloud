import { Question } from '../../api/client'

/** Brackets and case are noise when comparing a directive to its citation. */
function normalise(text: string): string {
  return text.replace(/[[\]]/g, '').trim().toLowerCase()
}

/**
 * Fragments of this question's source that the parser read but could not
 * interpret: bracketed scripter directives and unrecognised row labels, minus
 * whatever `extraction_evidence` already cites (that one WAS understood —
 * offering it back as "unread" would tell a founder the system still needs
 * teaching about something it already learned).
 *
 * Shared by TeachFormat's fragment picker and the "what the parser read"
 * review panel, so the two lists can never drift apart.
 */
export function unreadFragments(question: Question): string[] {
  const validation = (question.validation ?? {}) as Record<string, unknown>
  const directives = Array.isArray(validation.scripter_directives)
    ? (validation.scripter_directives as unknown[]).map(String)
    : []
  const rows = validation.unrecognised_rows
  const rowLabels =
    rows && typeof rows === 'object' && !Array.isArray(rows)
      ? Object.keys(rows as Record<string, unknown>)
      : []
  const cited = Object.values(question.extraction_evidence ?? {}).map(normalise)
  // Deduplicate: the same directive often appears on several questions, and a
  // repeated entry in the picker looks like two different things.
  return Array.from(new Set([...directives, ...rowLabels]))
    .filter(Boolean)
    .filter((fragment) => {
      const needle = normalise(fragment)
      return needle !== '' && !cited.some((c) => c.includes(needle))
    })
}
