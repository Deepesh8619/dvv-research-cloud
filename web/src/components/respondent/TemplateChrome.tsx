import type { Model } from 'survey-core'
import type { Theme } from '../../api/themes'
import { PROBE } from './probes'

// The header each non-Classic template draws above the question, and the Chat
// template's transcript. Pure presentation: everything here is read from the
// live SurveyJS model or from RespondentPage's position state.

interface HeaderProps {
  template: string
  theme: Theme
  title: string
  section: string
  at: number
  of: number
  /** 0-100. */
  progress: number
  segments: number
  stars: number
  big: boolean
  onBig: () => void
}

const INK = '#1F2333'
const GOLD = '#E0A526'

export function TemplateHeader(p: HeaderProps) {
  const count = `${p.at} of ${p.of}`
  if (p.template === 'ledger') {
    return (
      <div className="sticky top-0 z-20 bg-[#EEF1F4] pt-5">
        <div className="mx-auto flex max-w-[640px] items-baseline justify-between gap-3 border-b border-[#C9D0D9] px-4 pb-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-[#4E5664] sm:px-0">
          <span className="truncate">{[p.title, p.section].filter(Boolean).join(' · ') || 'Survey'}</span>
          <span className="shrink-0 tabular-nums">
            {String(p.at).padStart(2, '0')} / {String(p.of).padStart(2, '0')}
          </span>
        </div>
        <div className="mx-auto h-0.5 max-w-[640px] bg-[#C9D0D9]">
          <div className="h-full transition-[width] duration-300" style={{ width: `${p.progress}%`, background: p.theme.accent }} />
        </div>
      </div>
    )
  }
  if (p.template === 'chat') {
    return (
      <div className="sticky top-0 z-20 border-b border-[#E1E5EA] bg-white">
        <div className="mx-auto flex max-w-[640px] items-center gap-3 px-4 py-3 sm:px-0">
          <div
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[15px] font-bold text-white"
            style={{ background: p.theme.accentDeep }}
            aria-hidden
          >
            {(p.title || 'S').charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="truncate text-base font-bold text-[#1A1D23]">{p.title || 'Survey'}</p>
            <p className="text-xs text-[#5C6470]">Question {count}</p>
          </div>
        </div>
      </div>
    )
  }
  if (p.template === 'brand') {
    return (
      <div className="mx-auto max-w-[640px] px-6 pb-7 pt-6 text-white sm:px-0">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: GOLD }}>
            {p.section || 'Your views'}
          </span>
          <span className="text-xs font-semibold tabular-nums text-white/85">{count}</span>
        </div>
        <p className="mt-2 text-[22px] font-bold leading-tight">{p.title || 'Survey'}</p>
        <div className="mt-4 h-1 rounded-full bg-white/20">
          <div className="h-1 rounded-full transition-[width] duration-300" style={{ width: `${p.progress}%`, background: GOLD }} />
        </div>
      </div>
    )
  }
  if (p.template === 'play') {
    const done = Math.round((p.progress / 100) * p.segments)
    return (
      <div className="sticky top-0 z-20 bg-[#E9F5FF] pb-3 pt-4">
        <div className="mx-auto flex max-w-[640px] items-center gap-3 px-4 sm:px-0">
          <div className="flex flex-1 items-center" aria-label={`Question ${count}`}>
            {Array.from({ length: p.segments }, (_, i) => (
              <div key={i} className="flex flex-1 items-center last:flex-none">
                <span
                  className="h-4 w-4 shrink-0 rounded-full border-2"
                  style={{ borderColor: INK, background: i < done ? '#19A98A' : i === done ? '#FFC93C' : '#fff' }}
                />
                {i < p.segments - 1 && <span className="h-0.5 flex-1" style={{ background: i < done ? '#19A98A' : '#B9C7D6' }} />}
              </div>
            ))}
          </div>
          <span
            key={p.stars}
            className="dvv-stars inline-flex shrink-0 items-center gap-1 rounded-full border-2 bg-[#FFC93C] px-3 py-1 text-sm font-semibold tabular-nums"
            style={{ borderColor: INK, color: INK }}
            aria-label={`${p.stars} points`}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill={INK} aria-hidden>
              <path d="M12 2l3 6.9 7.5.7-5.7 5 1.7 7.4L12 18l-6.5 4 1.7-7.4-5.7-5 7.5-.7z" />
            </svg>
            {p.stars}
          </span>
        </div>
      </div>
    )
  }
  // clear
  return (
    <div className="mx-auto flex max-w-[640px] items-center justify-between gap-3 border-b-2 border-[#111] px-4 pb-3 pt-5 sm:px-0">
      <span className="font-bold">Question {count}</span>
      <button
        type="button"
        onClick={p.onBig}
        aria-pressed={p.big}
        className="min-h-[44px] rounded-lg border-2 border-[#111] px-3.5 text-base font-bold"
        style={{ background: p.big ? '#111' : '#fff', color: p.big ? '#fff' : '#111' }}
      >
        {p.big ? 'Text: larger' : 'Text: normal'}
      </button>
    </div>
  )
}

/** An answer as one line of text, whatever shape the question stores. */
export function answerText(value: unknown): string {
  if (value == null) return ''
  if (Array.isArray(value)) return value.map(answerText).join(', ')
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k}: ${answerText(v)}`)
      .join(' · ')
  }
  return String(value)
}

/** The last few questions answered before this one, as chat exchanges. */
export function Transcript({ model, qid, accent }: { model: Model; qid: string; accent: string }) {
  const asked = model.getAllQuestions(true).filter((q) => q.getType() !== 'html' && !q.name.endsWith(PROBE))
  const here = asked.findIndex((q) => q.name === qid)
  const past = asked.slice(0, Math.max(here, 0)).filter((q) => !q.isEmpty()).slice(-3)
  if (!past.length) return null
  return (
    <div className="mb-4 flex flex-col gap-2.5 opacity-80">
      {past.map((q) => {
        const answer = answerText(q.displayValue)
        return (
          <div key={q.name} className="flex flex-col gap-2.5">
            <p className="max-w-[85%] self-start rounded-[18px_18px_18px_6px] bg-white px-4 py-2.5 text-[15px] leading-snug text-[#1A1D23] shadow-sm">
              {q.processedTitle}
            </p>
            <p
              className="max-w-[78%] self-end rounded-[18px_18px_6px_18px] px-4 py-2.5 text-[15px] leading-snug text-white"
              style={{ background: accent }}
            >
              {answer.length > 140 ? `${answer.slice(0, 139)}…` : answer}
            </p>
          </div>
        )
      })}
    </div>
  )
}
