import { useState } from 'react'
import axios from 'axios'
import { questionnaireApi, type AgentSuggestion, type Question } from '../../api/client'
import { ChangeRow } from '../PreviewDiff'

/**
 * Fix this question with an agent. "Suggest a fix" asks the programmer agent
 * (no model); writing what is wrong asks the AI too. Each suggestion shows the
 * before/after and what QA finds on both. "Use this" puts it in the editor,
 * where it can still be rewritten before saving — and the next message to the
 * agent revises what is on screen, so it is a conversation, not one shot.
 */
export default function QuestionAgent({
  studyId,
  qid,
  current,
  onUse,
  onTeach,
}: {
  studyId: string
  qid: string
  current: () => Record<string, unknown>
  onUse: (q: Question) => void
  onTeach?: (text: string) => void
}) {
  const [instruction, setInstruction] = useState('')
  const [lastInstruction, setLastInstruction] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<AgentSuggestion[] | null>(null)
  const [used, setUsed] = useState<number | null>(null)

  const ask = async (withWords: boolean) => {
    setBusy(true)
    setError(null)
    setUsed(null)
    const words = withWords ? instruction.trim() : ''
    try {
      const r = await questionnaireApi.questionAgent(studyId, qid, {
        instruction: words || undefined,
        current: current(),
      })
      setSuggestions(r.data.suggestions)
      setError(r.data.error)
      if (words) {
        setLastInstruction(words)
        setInstruction('')
      }
    } catch (e) {
      setError(axios.isAxiosError(e) ? (e.response?.data?.detail ?? e.message) : 'The agent failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-sky-200 bg-sky-50/40 rounded-md p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-slate-800">Fix this question with the agent</span>
        <button
          onClick={() => ask(false)}
          disabled={busy}
          className="text-xs px-2.5 py-1 rounded border border-slate-300 bg-white hover:bg-slate-50 disabled:opacity-50"
        >
          Suggest a fix
        </button>
      </div>
      <div className="flex gap-2">
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && instruction.trim()) ask(true)
          }}
          rows={2}
          placeholder={
            suggestions
              ? 'Not quite? Tell it what to change next…'
              : 'Say what is wrong, e.g. “these are numbers that must add up to 100”'
          }
          className="flex-1 text-sm rounded border border-slate-300 px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-sky-500"
          aria-label="what is wrong with this question"
        />
        <button
          onClick={() => ask(true)}
          disabled={busy || !instruction.trim()}
          className="self-end text-sm px-3 py-1.5 rounded bg-slate-900 text-white hover:bg-slate-700 disabled:opacity-40"
        >
          {busy ? 'Thinking…' : 'Ask the agent'}
        </button>
      </div>
      {error && <p className="text-sm text-rose-700">{error}</p>}
      {suggestions && suggestions.length === 0 && !error && (
        <p className="text-sm text-slate-500">No fix to suggest. Say what is wrong and the agent will try.</p>
      )}
      {suggestions && suggestions.length > 0 && (
        <div className="max-h-[26rem] overflow-y-auto space-y-2 pr-1">
          {suggestions.map((s, i) => (
            <div key={i} className="bg-white border border-slate-200 rounded-md p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm text-slate-800">{s.explanation}</p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {s.source} · QA:{' '}
                    <span className={s.qa_after.length ? 'text-amber-700' : 'text-emerald-700'}>
                      {s.qa_before.length} problem{s.qa_before.length === 1 ? '' : 's'} →{' '}
                      {s.qa_after.length === 0 ? 'none' : s.qa_after.length}
                    </span>
                  </p>
                </div>
                <div className="flex gap-1.5 shrink-0">
                  <button
                    onClick={() => {
                      onUse(s.question)
                      setUsed(i)
                    }}
                    className="text-xs px-2.5 py-1 rounded bg-emerald-700 text-white hover:bg-emerald-800"
                  >
                    {used === i ? 'In the editor ✓' : 'Use this'}
                  </button>
                </div>
              </div>
              {s.qa_after.length > 0 && (
                <ul className="mt-1 text-xs text-amber-800 list-disc pl-4">
                  {s.qa_after.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              )}
              {s.dropped.length > 0 && (
                <p className="mt-1 text-xs text-rose-700">
                  Not used — not in the document or your words: {s.dropped.join(', ')}
                </p>
              )}
              <div className="mt-1.5">
                <ChangeRow q={s.change} defaultOpen />
              </div>
            </div>
          ))}
        </div>
      )}
      {used !== null && (
        <p className="text-xs text-slate-600">
          It is in the editor below — change anything, then Save.
          {onTeach && lastInstruction && (
            <>
              {' '}
              <button className="underline text-sky-700" onClick={() => onTeach(lastInstruction)}>
                Teach the parser this
              </button>{' '}
              so the next document gets it right.
            </>
          )}
        </p>
      )}
    </div>
  )
}
