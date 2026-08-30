import { useCallback, useEffect, useState } from 'react'
import PreviewDiff from './PreviewDiff'
import { useLabel } from '../labels/LabelContext'
import {
  correctionsApi,
  type CorrectionResult,
  type CorrectionSuggestion,
  type ProfileRule,
} from '../api/client'

/**
 * The founders' correction surface.
 *
 * A correction here is not an edit to one question — it is a sentence about how
 * a client writes questionnaires ("terminates live in the TERM row"), which can
 * become a rule applied to every question in the document and to every future
 * document of the same format. The scope chip is the element that makes that
 * visible at the moment of writing; without it every correction quietly
 * defaults to a one-off and the parser never learns anything.
 */

const SCOPES = [
  { id: 'profile', label: 'All documents of this format', hint: 'Teaches the parser permanently' },
  { id: 'document', label: 'Just this document', hint: 'Applies to this study only' },
  { id: 'question', label: 'Only this question', hint: 'A genuine one-off' },
] as const

const SOURCE_LABEL: Record<CorrectionSuggestion['source'], string> = {
  flagged: 'Flagged',
  learned: 'Learned',
  common: 'Common',
}

const SOURCE_STYLE: Record<CorrectionSuggestion['source'], string> = {
  flagged: 'text-amber-700 bg-amber-50 border-amber-200',
  learned: 'text-sky-700 bg-sky-50 border-sky-200',
  common: 'text-slate-500 bg-slate-50 border-slate-200',
}

interface Props {
  studyId: string
  questionId?: string
  /** Detected format profile, shown so the author knows what a profile-scoped rule teaches. */
  profileSlug?: string | null
  profileLabel?: string | null
  onApplied?: () => void
}

export default function CorrectionBox({
  studyId,
  questionId,
  profileSlug,
  profileLabel,
  onApplied,
}: Props) {
  const tTrigger = useLabel('correction.trigger', 'Correct this')
  const tHeading = useLabel('correction.heading', 'What did we read wrong?')
  const tSubmit = useLabel('correction.submit', 'Send correction')
  const tScope = useLabel('correction.scopeLabel', 'This applies to')
  const tSuggest = useLabel('correction.suggestLabel', 'Suggested — click to fill, then edit')
  const [open, setOpen] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [scope, setScope] = useState<string>('profile')
  const [suggestedFrom, setSuggestedFrom] = useState('freeform')
  const [suggestions, setSuggestions] = useState<CorrectionSuggestion[]>([])
  const [result, setResult] = useState<CorrectionResult | null>(null)
  const [rules, setRules] = useState<ProfileRule[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const slug = result?.profile_slug ?? profileSlug ?? null

  useEffect(() => {
    if (!open) return
    correctionsApi
      .suggestions(studyId, questionId)
      .then((r) => setSuggestions(r.data.suggestions))
      .catch(() => setSuggestions([]))
  }, [open, studyId, questionId])

  const loadRules = useCallback(() => {
    if (!slug) return
    correctionsApi
      .rules(slug)
      .then((r) => setRules(r.data.rules))
      .catch(() => setRules([]))
  }, [slug])

  useEffect(() => {
    if (open) loadRules()
  }, [open, loadRules])

  const submit = async () => {
    if (!instruction.trim()) return
    setBusy(true)
    setError(null)
    try {
      const r = await correctionsApi.create(studyId, {
        instruction: instruction.trim(),
        question_id: questionId,
        scope,
        suggested_from: suggestedFrom,
      })
      setResult(r.data)
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Could not send that correction. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (rule: ProfileRule) => {
    if (!slug) return
    await correctionsApi.toggleRule(slug, rule.id).catch(() => undefined)
    loadRules()
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="text-xs font-medium text-sky-700 hover:text-sky-900 hover:underline
                   focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500
                   rounded px-1 py-0.5 transition"
      >
        {tTrigger}
      </button>
    )
  }

  return (
    <div className="mt-3 border border-sky-200 bg-sky-50/40 rounded-md p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h4 className="text-sm font-semibold text-slate-800">
          {tHeading}
        </h4>
        <button
          onClick={() => {
            setOpen(false)
            setResult(null)
            setError(null)
          }}
          className="text-xs text-slate-500 hover:text-slate-800"
        >
          Close
        </button>
      </div>

      {!result && (
        <>
          <p className="text-xs text-slate-600 leading-relaxed">
            Describe the mistake the way you'd explain it to a colleague. If it's about how this
            client writes questionnaires, say so — that teaches the parser for every future document.
          </p>

          <textarea
            value={instruction}
            onChange={(e) => {
              setInstruction(e.target.value)
              if (suggestedFrom !== 'freeform' && !e.target.value) setSuggestedFrom('freeform')
            }}
            rows={3}
            placeholder="e.g. The terminate condition wasn't identified — in this client's documents it's always in the TERM row under the options."
            className="w-full text-sm rounded border border-slate-300 px-3 py-2 bg-white
                       focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-sky-500
                       placeholder:text-slate-400"
          />

          {suggestions.length > 0 && (
            <div>
              <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1.5">
                {tSuggest}
              </p>
              <div className="flex flex-col gap-1.5 max-h-48 overflow-y-auto">
                {suggestions.map((s, i) => (
                  <button
                    key={`${s.source}-${i}`}
                    onClick={() => {
                      setInstruction(s.text)
                      setSuggestedFrom(s.source)
                    }}
                    className="text-left text-xs bg-white border border-slate-200 rounded
                               px-2.5 py-2 hover:border-sky-300 hover:bg-sky-50 transition
                               focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500
                               flex gap-2 items-start"
                  >
                    <span
                      className={`shrink-0 text-[9px] font-mono uppercase tracking-wide
                                  px-1.5 py-0.5 rounded border ${SOURCE_STYLE[s.source]}`}
                    >
                      {SOURCE_LABEL[s.source]}
                    </span>
                    <span className="text-slate-700 leading-snug">{s.text}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="pt-2 border-t border-sky-200/70">
            <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1.5">
              {tScope}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {SCOPES.map((s) => (
                <button
                  key={s.id}
                  onClick={() => setScope(s.id)}
                  title={s.hint}
                  className={`text-xs px-2.5 py-1 rounded border transition
                    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500
                    ${
                      scope === s.id
                        ? 'bg-sky-100 border-sky-400 text-sky-900 font-medium'
                        : 'bg-white border-slate-200 text-slate-500 hover:border-slate-300'
                    }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
            {scope === 'profile' && (
              <p className="text-[11px] text-sky-800 mt-1.5">
                Will apply to every {profileLabel ?? slug ?? 'document of this format'} from now on.
              </p>
            )}
          </div>

          {error && <p className="text-xs text-rose-700">{error}</p>}

          <button
            onClick={submit}
            disabled={busy || !instruction.trim()}
            className="text-sm font-medium bg-slate-900 text-white px-4 py-1.5 rounded
                       hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed
                       active:scale-[0.98] transition
                       focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
          >
            {busy ? 'Reading…' : tSubmit}
          </button>
        </>
      )}

      {result && (
        <div className="space-y-3">
          <div
            className={`text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded inline-block
              ${
                result.lane === 'live'
                  ? 'bg-emerald-100 text-emerald-800'
                  : 'bg-amber-100 text-amber-800'
              }`}
          >
            {result.lane === 'live' ? 'Can be applied now' : 'Queued for engineering'}
          </div>

          <div className="bg-white border border-slate-200 rounded p-3">
            <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1">
              The rule we'd remember
            </p>
            <p className="text-sm text-slate-800 leading-snug">{result.rule_text}</p>
            {result.rationale && (
              <p className="text-xs text-slate-500 mt-2 leading-snug">{result.rationale}</p>
            )}
          </div>

          {result.degraded && (
            <p className="text-xs text-amber-800">
              The model was unreachable, so this was saved and queued rather than applied.
            </p>
          )}

          {error && <p className="text-xs text-rose-700">{error}</p>}

          <div className="flex gap-2">
            <button
              onClick={() => setResult(null)}
              className="text-sm text-slate-600 px-3 py-1.5 rounded border border-slate-300
                         hover:bg-slate-50 active:scale-[0.98] transition"
            >
              Rewrite it
            </button>
          </div>

          {/* Live-lane rules are approved from the DIFF, never blind: the
              preview re-reads the whole document and shows what got worse as
              well as what got better. */}
          {result.lane === 'live' && (
            <PreviewDiff
              studyId={studyId}
              correctionId={result.id}
              onApplied={() => {
                setResult(null)
                setInstruction('')
                loadRules()
                onApplied?.()
              }}
              // A send-back returns the model's fresh attempt; show it in place
              // so the reviewer sees the new rule without losing their thread.
              onRevised={(revised) => setResult(revised)}
            />
          )}
        </div>
      )}

      {rules.length > 0 && (
        <div className="pt-3 border-t border-sky-200/70">
          <p className="text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-1.5">
            What this parser has been taught ({rules.filter((r) => r.active).length} active)
          </p>
          <ul className="space-y-1">
            {rules.map((r) => (
              <li key={r.id} className="flex items-start gap-2 text-xs">
                <button
                  onClick={() => toggle(r)}
                  title={r.active ? 'Switch this rule off' : 'Switch this rule back on'}
                  className={`shrink-0 mt-0.5 w-8 text-[9px] font-mono uppercase rounded px-1 py-0.5
                    border transition focus-visible:outline-none focus-visible:ring-2
                    focus-visible:ring-sky-500 ${
                      r.active
                        ? 'bg-emerald-50 border-emerald-300 text-emerald-800'
                        : 'bg-slate-100 border-slate-300 text-slate-400'
                    }`}
                >
                  {r.active ? 'on' : 'off'}
                </button>
                <span className={r.active ? 'text-slate-700' : 'text-slate-400 line-through'}>
                  {r.rule_text}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
