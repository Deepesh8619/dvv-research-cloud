import 'survey-core/defaultV2.min.css'

import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Model } from 'survey-core'
import { BorderlessLight } from 'survey-core/themes'
import { Survey } from 'survey-react-ui'
import { fieldingApi } from '../api/client'
import { Theme, themeFor } from '../api/themes'
import {
  ArrowRightIcon,
  CircleCheckIcon,
  ClipboardIcon,
  ClockIcon,
  InfoIcon,
  ListIcon,
} from '../components/Icons'
import LoadingSpinner from '../components/LoadingSpinner'

// A single respondent view: fetch the compiled SurveyJS JSON for a public
// fielding token, render it ONE QUESTION PER PAGE, and stream answers back to
// the server on every page advance (the incremental-capture requirement).
//
// Terminates are enforced SERVER-SIDE: each page POST returns `terminated` when a
// `terminate_if` fires against the accumulated answers, at which point we HALT the
// survey mid-interview and swap to the screen-out view — the client never decides
// who qualifies. Because every question is its own page, the terminate check now
// runs after each answer instead of only at completion (the pilot's core bug: a
// single-block survey was one page, so the mid-survey POST never fired).
//
// Every fetch failure lands in a visible state (never a blank screen): each of
// welcome / loading / error / closed / terminated / complete is an explicit render.

type Phase = 'loading' | 'welcome' | 'error' | 'closed' | 'paused' | 'running' | 'terminated' | 'complete'

// Per-segment theme keyed by the survey's optional metadata.theme slug (T24).
// Absent/unknown -> the DVV Indigo default. Presentation only; no logic depends
// on it. THEMES is the ONE source of truth, shared with the pickers.
const DEFAULT_THEME = themeFor(null)

// The 4px card top strip — the one deliberate flourish per the design spec.
function gradientBar(theme: Theme): string {
  return `linear-gradient(90deg, ${theme.accent}, ${theme.accentDeep})`
}

// One polished default theme + the segment accent, applied via applyTheme. Keeps
// the modern-survey look (styled inputs, progress bar) tied to the accent; the
// deep tone drives pressed/hover, the ~10%-alpha light drives the selected tint.
function applySurveyTheme(survey: Model, theme: Theme): void {
  survey.applyTheme({
    ...BorderlessLight,
    cssVariables: {
      ...BorderlessLight.cssVariables,
      '--sjs-primary-backcolor': theme.accent,
      '--sjs-primary-backcolor-dark': theme.accentDeep,
      '--sjs-primary-backcolor-light': `${theme.accent}1A`, // ~10% alpha selected tint
    },
  })
}

function Centered({ theme, children }: { theme: Theme; children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ background: theme.wash }}>
      <div className="w-full max-w-[640px] text-center bg-white rounded-2xl shadow-md border border-slate-200/80 overflow-hidden">
        <div style={{ height: 4, background: gradientBar(theme) }} />
        <div className="p-10 sm:p-12">{children}</div>
      </div>
    </div>
  )
}

// Accent-tinted emblem: a soft accent wash square with a hairline accent ring,
// the icon inheriting the accent via currentColor. One per terminal screen.
function Emblem({ theme, children }: { theme: Theme; children: React.ReactNode }) {
  return (
    <div
      className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl"
      style={{ background: `${theme.accent}14`, color: theme.accent, boxShadow: `inset 0 0 0 1px ${theme.accent}29` }}
    >
      {children}
    </div>
  )
}

function PoweredBy() {
  return <p className="mt-8 text-xs font-medium tracking-wide text-slate-400">Powered by DVV Research Cloud</p>
}

export default function RespondentPage() {
  const { token } = useParams<{ token: string }>()
  const [phase, setPhase] = useState<Phase>('loading')
  const [error, setError] = useState('')
  const [model, setModel] = useState<Model | null>(null)
  const [title, setTitle] = useState('')
  const [theme, setTheme] = useState<Theme>(DEFAULT_THEME)
  // Live refs the async event handlers read (state would be stale in closures).
  const sessionIdRef = useRef<string>('')
  const terminatedRef = useRef(false)

  useEffect(() => {
    let cancelled = false

    // Any terminal API response may carry a vendor redirect URL. Show the
    // terminal screen briefly, then hand the respondent back to the panel.
    const goRedirect = (url?: string | null) => {
      if (url) setTimeout(() => (window.location.href = url), 1200)
    }

    const load = async () => {
      if (!token) {
        setError('Missing survey link.')
        setPhase('error')
        return
      }
      try {
        const res = await fieldingApi.enter(token, window.location.search)
        if (cancelled) return
        if (res.data.status === 'closed') {
          setPhase('closed')
          return
        }
        // Soft-launch hold: the link is live but not admitting anyone right now.
        // A respondent must never be dropped into an empty survey model, and a
        // panel expects its redirect, so bounce when one is configured.
        if (res.data.status === 'paused') {
          setPhase('paused')
          goRedirect(res.data.redirect_url)
          return
        }
        // Terminal-at-entry (duplicate-pid security = 30): don't serve the survey.
        if (res.data.status_code != null) {
          terminatedRef.current = true
          setPhase('terminated')
          goRedirect(res.data.redirect_url)
          return
        }
        sessionIdRef.current = res.data.session_id
        const surveyTheme = themeFor(res.data.theme)
        const survey = new Model(res.data.surveyjs_json)

        // One question per page: this is what makes the per-page answer POST (and
        // therefore the server-side terminate check) fire mid-survey.
        survey.questionsOnPageMode = 'questionPerPage'
        survey.goNextPageAutomatic = true // auto-advance on radio/dropdown select
        survey.showProgressBar = 'top'
        survey.progressBarType = 'questions'
        survey.showQuestionNumbers = 'off'
        survey.focusFirstQuestionAutomatic = true
        // We swap to our own warm thank-you view on complete, so suppress SurveyJS's.
        survey.showCompletedPage = false
        applySurveyTheme(survey, surveyTheme)

        // Per-page incremental capture: POST accumulated answers on every advance.
        // Multi/checkbox/text don't auto-advance — their Next click fires this too.
        survey.onCurrentPageChanged.add(async (sender) => {
          await submitPage(sender, sender.currentPageNo)
        })

        // Final page + finish: POST the last page's answers, then mark complete.
        survey.onComplete.add(async (sender) => {
          const terminated = await submitPage(sender, sender.currentPageNo)
          if (terminated || terminatedRef.current) return
          try {
            const res = await fieldingApi.complete(token, sessionIdRef.current)
            // Complete may screen out at complete-time on overquota (40).
            if (res.data.status_code === 40) {
              terminatedRef.current = true
              setPhase('terminated')
            } else {
              setPhase('complete')
            }
            goRedirect(res.data.redirect_url)
          } catch {
            setError('We could not save your final response. Please try again.')
            setPhase('error')
          }
        })

        setModel(survey)
        setTitle(survey.title || 'Survey')
        setTheme(surveyTheme)
        setPhase('welcome')
      } catch {
        if (cancelled) return
        setError('This survey could not be loaded. The link may be invalid.')
        setPhase('error')
      }
    }

    // Posts sender.data to the server; returns true if the server terminated us.
    const submitPage = async (sender: Model, pageNo: number): Promise<boolean> => {
      if (!token || terminatedRef.current) return terminatedRef.current
      try {
        const res = await fieldingApi.answers(
          token,
          sessionIdRef.current,
          sender.data as Record<string, unknown>,
          pageNo,
        )
        if (res.data.terminated) {
          terminatedRef.current = true
          setPhase('terminated') // unmounts <Survey/> -> survey halts immediately
          goRedirect(res.data.redirect_url)
          return true
        }
        return false
      } catch {
        // A transient save failure must be visible, not silent.
        setError('We hit a problem saving your answers. Please check your connection.')
        setPhase('error')
        return false
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [token])

  if (phase === 'loading') return <LoadingSpinner />

  if (phase === 'error') {
    return (
      <Centered theme={theme}>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">Something went wrong</h1>
        <p className="text-slate-600">{error || 'Please try again later.'}</p>
      </Centered>
    )
  }

  if (phase === 'closed') {
    return (
      <Centered theme={theme}>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">Survey closed</h1>
        <p className="text-slate-600">
          This survey is no longer accepting responses. Thank you for your interest.
        </p>
      </Centered>
    )
  }

  if (phase === 'paused') {
    return (
      <Centered theme={theme}>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">Not accepting responses right now</h1>
        <p className="text-slate-600">
          This survey has paused while responses are reviewed. Please try your link again later.
        </p>
      </Centered>
    )
  }

  if (phase === 'welcome') {
    const questionCount = model?.getAllQuestions().length ?? 0
    const minutes = Math.max(1, Math.ceil(questionCount / 4))
    return (
      <Centered theme={theme}>
        <Emblem theme={theme}>
          <ClipboardIcon className="w-8 h-8" />
        </Emblem>
        <h1 className="text-2xl font-semibold text-slate-900 mb-3">{title}</h1>
        <p className="text-slate-600 mb-7 max-w-sm mx-auto">
          Your answers are confidential. There are no right or wrong responses — please answer honestly.
        </p>
        {questionCount > 0 && (
          <div className="mb-8 flex items-center justify-center gap-4 text-sm text-slate-500">
            <span className="inline-flex items-center gap-1.5 tabular-nums">
              <ListIcon className="w-4 h-4" />
              {questionCount} {questionCount === 1 ? 'question' : 'questions'}
            </span>
            <span className="h-3.5 w-px bg-slate-200" />
            <span className="inline-flex items-center gap-1.5 tabular-nums">
              <ClockIcon className="w-4 h-4" />
              about {minutes} min
            </span>
          </div>
        )}
        <button
          onClick={() => setPhase('running')}
          className="group inline-flex items-center justify-center gap-2 rounded-lg px-8 py-3 text-white font-medium shadow-sm transition-all duration-200 ease-out hover:opacity-95 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
          style={{ backgroundColor: theme.accent, ['--tw-ring-color' as string]: theme.accent }}
        >
          Start
          <ArrowRightIcon className="w-5 h-5 transition-transform duration-200 ease-out group-hover:translate-x-0.5" />
        </button>
        <PoweredBy />
      </Centered>
    )
  }

  if (phase === 'terminated') {
    // Calm, neutral screen-out — deliberately NOT the accent (never a red alarm).
    return (
      <Centered theme={theme}>
        <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100 text-slate-500 ring-1 ring-inset ring-slate-200">
          <InfoIcon className="w-8 h-8" />
        </div>
        <h1 className="text-xl font-semibold text-slate-900 mb-2">Not a match this time</h1>
        <p className="text-slate-600 max-w-sm mx-auto">
          Based on your answers, this particular survey isn't the right fit. Thank you for your time — we truly
          appreciate it.
        </p>
        <PoweredBy />
      </Centered>
    )
  }

  if (phase === 'complete') {
    return (
      <Centered theme={theme}>
        <Emblem theme={theme}>
          <CircleCheckIcon className="w-8 h-8" />
        </Emblem>
        <h1 className="text-2xl font-semibold text-slate-900 mb-2">Thank you!</h1>
        <p className="text-slate-600 max-w-sm mx-auto">
          Your responses have been recorded. We really appreciate you taking the time.
        </p>
        <PoweredBy />
      </Centered>
    )
  }

  // running — one question per page, centered card
  return (
    <div className="min-h-screen py-8" style={{ background: theme.wash }}>
      {/* Scoped polish: larger question type + touch-friendly, full-width options.
          The selected option row is tinted with the theme accent (~10% alpha bg +
          accent border) — the accent CSS var comes from applySurveyTheme. */}
      <style>{`
        .dvv-survey .sd-question__title { font-size: 1.25rem; line-height: 1.75rem; }
        .dvv-survey .sd-item {
          padding: 0.75rem 1rem; min-height: 44px; border-radius: 0.5rem; border: 1px solid transparent;
          transition: background 150ms ease-out, border-color 150ms ease-out, box-shadow 150ms ease-out;
        }
        .dvv-survey .sd-item:hover { background: rgba(0,0,0,0.03); }
        .dvv-survey .sd-item--checked { background: ${theme.accent}1A; border-color: ${theme.accent}; box-shadow: inset 0 0 0 1px ${theme.accent}; }
        .dvv-survey .sd-selectbase__label { width: 100%; }
        /* Slim, rounded progress bar; tabular figures on its label. */
        .dvv-survey .sd-progress { height: 6px; border-radius: 9999px; }
        .dvv-survey .sd-progress__bar { border-radius: 9999px; transition: width 250ms ease-out; }
        .dvv-survey .sd-progress__text { font-variant-numeric: tabular-nums; letter-spacing: 0.01em; }
        @media (prefers-reduced-motion: reduce) {
          .dvv-survey .sd-item, .dvv-survey .sd-progress__bar { transition: none; }
        }
      `}</style>
      <div className="dvv-survey max-w-[640px] mx-auto px-4">
        <div className="bg-white rounded-2xl shadow-md border border-slate-200/80 overflow-hidden">
          <div style={{ height: 4, background: gradientBar(theme) }} />
          <div className="p-6 sm:p-8">{model && <Survey model={model} />}</div>
        </div>
      </div>
    </div>
  )
}
