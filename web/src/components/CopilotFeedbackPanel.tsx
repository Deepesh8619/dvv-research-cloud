import { useEffect, useState } from 'react'
import { copilotApi, errorMessage, FlaggedCopilotAnswer } from '../api/client'

/** The eval-set entry this flag should become (evals/copilot/cases.json); expectations are filled by hand. */
const draftCase = (f: FlaggedCopilotAnswer) =>
  JSON.stringify(
    {
      id: `fb-${f.conversation_id.replace(/^conv_/, '')}-${f.seq}`,
      category: 'feedback',
      study: f.study_id,
      why: f.note || 'flagged as wrong',
      turns: [{ say: f.question, expect: { tools_any: f.lookups.map((l) => l.tool) } }],
    },
    null,
    1,
  )

/** Answers people flagged as wrong: what was asked, what came back, and what they said. */
export default function CopilotFeedbackPanel() {
  const [items, setItems] = useState<FlaggedCopilotAnswer[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)

  useEffect(() => {
    copilotApi
      .flagged()
      .then((res) => setItems(res.data))
      .catch((err) => setError(errorMessage(err) || 'Could not load the flagged answers.'))
  }, [])

  const copy = async (f: FlaggedCopilotAnswer) => {
    const key = `${f.conversation_id}-${f.seq}`
    try {
      await navigator.clipboard.writeText(draftCase(f))
      setCopied(key)
    } catch {
      setCopied(null)
    }
  }

  if (error) return <p className="text-sm text-red-600">{error}</p>
  if (!items) return <p className="text-sm text-slate-500">Loading…</p>
  if (items.length === 0)
    return (
      <p className="text-sm text-slate-500">
        No answers flagged yet. Anyone can flag a Copilot reply with "Wrong answer?" under it; flags appear here.
      </p>
    )

  return (
    <ul className="space-y-3" data-testid="copilot-feedback">
      {items.map((f) => {
        const key = `${f.conversation_id}-${f.seq}`
        return (
          <li key={key} className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs text-slate-500">
              <span>
                {f.user ?? 'someone'}
                {f.study_id ? ` · ${f.study_id}` : ''}
                {f.flagged_at ? ` · ${new Date(f.flagged_at).toLocaleString()}` : ''}
              </span>
              <button type="button" onClick={() => copy(f)} className="font-medium text-primary-600 hover:underline">
                {copied === key ? 'Copied' : 'Copy as test case'}
              </button>
            </div>
            <p className="mt-2 font-medium text-slate-900">{f.question}</p>
            {f.note && <p className="mt-1 text-amber-800">“{f.note}”</p>}
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-slate-500">
                The answer{f.lookups.length ? ` · tools: ${f.lookups.map((l) => l.tool).join(', ')}` : ' · no tools'}
                {f.answered_by ? ` · ${f.answered_by}` : ''}
              </summary>
              <p className="mt-1 whitespace-pre-wrap text-slate-700">{f.reply}</p>
            </details>
          </li>
        )
      })}
    </ul>
  )
}
