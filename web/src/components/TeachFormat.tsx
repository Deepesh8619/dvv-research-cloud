import { useEffect, useState } from 'react'
import axios from 'axios'
import { PatternKind, Question, correctionsApi } from '../api/client'
import { typeLabel } from '../labels/questionTypes'
import { unreadFragments } from './review/unreadFragments'

/**
 * Bind a convention this document uses to what it means, once, for this format.
 *
 * The correction loop already taught the model: an approved rule is prose
 * appended to the extraction prompt. That makes the system more accurate and
 * never cheaper — the same convention on the next client document still costs
 * a model call, every time.
 *
 * What is taught here is read by the deterministic parsers instead, so the
 * second document using the convention is parsed for free.
 *
 * The fragments offered are the ones THIS question actually contains and the
 * parsers did not understand. Picking from that list rather than retyping is
 * the difference between teaching the real string and teaching a typo that
 * silently never matches.
 */
interface Props {
  profileSlug: string
  profileLabel?: string | null
  question: Question
  onTaught?: () => void
  /**
   * Requested from elsewhere on the review screen — an unread fragment's
   * "Teach this" button, or the blank-question triage — so the founder never
   * retypes a fragment they can already see. `nonce` only needs to change; it
   * exists so clicking the same fragment twice in a row still reopens.
   */
  openRequest?: { fragment: string; nonce: number } | null
}

export default function TeachFormat({
  profileSlug,
  profileLabel,
  question,
  onTaught,
  openRequest,
}: Props) {
  const [open, setOpen] = useState(false)
  const [kinds, setKinds] = useState<PatternKind[]>([])
  const [kind, setKind] = useState('type_signal')
  const [fragment, setFragment] = useState('')
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [taught, setTaught] = useState<string | null>(null)

  const candidates = unreadFragments(question)

  useEffect(() => {
    if (!open || kinds.length > 0) return
    correctionsApi
      .patternKinds()
      .then((r) => setKinds(r.data.kinds))
      .catch(() => setError('Could not load the pattern types.'))
  }, [open, kinds.length])

  useEffect(() => {
    if (!openRequest) return
    setOpen(true)
    if (openRequest.fragment) setFragment(openRequest.fragment)
    // Only the nonce needs to be a dependency — see the prop's doc comment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRequest?.nonce])

  const selected = kinds.find((k) => k.kind === kind)
  // randomize_signal carries no meaning beyond its own presence.
  const needsValue = (selected?.values.length ?? 0) > 0

  const submit = async () => {
    setSaving(true)
    setError(null)
    try {
      const res = await correctionsApi.applyRule(profileSlug, {
        pattern_kind: kind,
        pattern: fragment.trim(),
        pattern_value: needsValue ? value : undefined,
      })
      setTaught(res.data.rule_text)
      setFragment('')
      setValue('')
      onTaught?.()
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
      setError(typeof detail === 'string' ? detail : 'Could not save this pattern.')
    } finally {
      setSaving(false)
    }
  }

  const canSubmit = fragment.trim().length >= 2 && (!needsValue || !!value) && !saving

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="text-xs text-sky-700 hover:text-sky-900 underline underline-offset-2"
      >
        Teach this format
        {candidates.length > 0 ? ` (${candidates.length} not understood)` : ''}
      </button>
    )
  }

  return (
    <section className="rounded-md border border-sky-200 bg-sky-50/60 p-3 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-sky-900">
            Teach this format
          </h3>
          <p className="text-xs text-slate-600 mt-0.5">
            Applies to every document detected as{' '}
            <span className="font-medium">{profileLabel || profileSlug}</span>, from now
            on — read directly by the parser, with no AI call.
          </p>
        </div>
        <button
          onClick={() => setOpen(false)}
          className="text-xs text-slate-500 hover:text-slate-700"
        >
          Close
        </button>
      </div>

      {taught && (
        <p className="text-xs text-emerald-800 bg-emerald-50 border border-emerald-200 rounded px-2 py-1.5">
          Learned: {taught}
        </p>
      )}

      <label className="block">
        <span className="text-xs text-slate-600">What does it look like?</span>
        {candidates.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-1 mb-1.5">
            {candidates.map((c) => (
              <button
                key={c}
                onClick={() => setFragment(c)}
                title="Use this fragment from the question"
                className={`text-[11px] font-mono px-2 py-1 rounded border ${
                  fragment === c
                    ? 'border-sky-500 bg-white text-sky-900'
                    : 'border-slate-300 bg-white text-slate-700 hover:border-sky-400'
                }`}
              >
                {c.length > 44 ? `${c.slice(0, 44)}…` : c}
              </button>
            ))}
          </div>
        )}
        <input
          value={fragment}
          onChange={(e) => setFragment(e.target.value)}
          placeholder="e.g. [SHOWCARD]"
          className="w-full h-8 px-2 rounded border border-slate-300 text-sm font-mono"
        />
      </label>

      <label className="block">
        <span className="text-xs text-slate-600">What does it mean?</span>
        <select
          value={kind}
          onChange={(e) => {
            setKind(e.target.value)
            setValue('')
          }}
          className="w-full h-8 px-2 rounded border border-slate-300 text-sm mt-1"
        >
          {kinds.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </select>
        {selected && <p className="text-[11px] text-slate-500 mt-1">{selected.help}</p>}
      </label>

      {needsValue && (
        <label className="block">
          <span className="text-xs text-slate-600">Set it to</span>
          <select
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="w-full h-8 px-2 rounded border border-slate-300 text-sm mt-1"
          >
            <option value="">choose…</option>
            {selected?.values.map((v) => (
              <option key={v} value={v}>
                {kind === 'type_signal' ? typeLabel(v) : v}
              </option>
            ))}
          </select>
        </label>
      )}

      {error && <p className="text-xs text-red-700">{error}</p>}

      <button
        onClick={submit}
        disabled={!canSubmit}
        className="text-xs bg-sky-700 text-white font-medium px-3 py-1.5 rounded hover:bg-sky-800 disabled:opacity-50"
      >
        {saving ? 'Teaching…' : 'Teach it'}
      </button>
    </section>
  )
}
