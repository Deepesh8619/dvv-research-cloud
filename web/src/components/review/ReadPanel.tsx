import { Question } from '../../api/client'
import { unreadFragments } from './unreadFragments'

/**
 * "What the parser read / could not read" — two columns built entirely from
 * data the question already carries. A question that came out empty or wrong
 * used to just look blank: nothing said what the parser could not make sense
 * of, or which lesson would fix it. Left column is `extraction_evidence`
 * as-is (field -> the fragment that decided it); right is whatever source
 * text was left over — the same "unread fragments" TeachFormat already
 * offers as a picker, reused here via `unreadFragments`.
 */
interface Props {
  question: Question
  /** Only a founder with a detected profile can act on an unread fragment. */
  canTeach: boolean
  onTeach: (fragment: string) => void
}

function prettify(key: string): string {
  return key.replace(/_/g, ' ')
}

export default function ReadPanel({ question, canTeach, onTeach }: Props) {
  const evidence = Object.entries(question.extraction_evidence ?? {})
  const unread = unreadFragments(question)

  // Nothing to say — a clean question with no citations and no leftovers.
  // Quiet by default beats a wall of text on every question.
  if (evidence.length === 0 && unread.length === 0) return null

  return (
    <section
      aria-label="What the parser read"
      className="rounded-md border border-slate-200 bg-white p-3"
    >
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600 mb-2">
        What the parser read
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-[11px] font-medium text-slate-500 mb-1">Read</p>
          {evidence.length > 0 ? (
            <dl className="space-y-1">
              {evidence.map(([field, fragment]) => (
                <div key={field} className="text-xs">
                  <dt className="text-slate-500 inline">{prettify(field)}: </dt>
                  <dd className="inline font-mono text-slate-700 break-words">{fragment}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-xs text-slate-400">Nothing cited.</p>
          )}
        </div>
        <div>
          <p className="text-[11px] font-medium text-slate-500 mb-1">Could not read</p>
          {unread.length > 0 ? (
            <ul className="space-y-1.5">
              {unread.map((f) => (
                <li key={f} className="flex items-start justify-between gap-2 text-xs">
                  <span className="font-mono text-slate-700 break-words">{f}</span>
                  {canTeach && (
                    <button
                      type="button"
                      onClick={() => onTeach(f)}
                      className="shrink-0 text-sky-700 hover:text-sky-900 underline underline-offset-2"
                    >
                      Teach this
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-emerald-700">Everything in the source was read.</p>
          )}
        </div>
      </div>
    </section>
  )
}
