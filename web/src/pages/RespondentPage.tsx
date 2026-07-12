import 'survey-core/defaultV2.min.css'

import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Model } from 'survey-core'
import { BorderlessLight } from 'survey-core/themes'
import { Survey } from 'survey-react-ui'
import { fieldingApi } from '../api/client'
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

type Phase = 'loading' | 'welcome' | 'error' | 'closed' | 'running' | 'terminated' | 'complete'

// Per-segment accent, keyed by the survey's optional metadata.theme slug. Absent
// or unknown -> a neutral indigo default. Presentation only; no logic depends on it.
const ACCENTS: Record<string, string> = {
  fmcg: '#16a34a', // fresh green / mint
  healthcare: '#0284c7', // calm clinical blue
  bfsi: '#1e3a8a', // deep trust navy
  auto: '#dc2626', // graphite + bold red
  telecom: '#7c3aed', // vibrant violet
  retail: '#ea580c', // warm orange
}
const DEFAULT_ACCENT = '#4f46e5'

function accentFor(theme?: string | null): string {
  return (theme && ACCENTS[theme]) || DEFAULT_ACCENT
}

// One polished default theme + the segment accent, applied via applyTheme. Keeps
// the modern-survey look (styled inputs, buttons, progress bar) tied to the accent.
function applySurveyTheme(survey: Model, accent: string): void {
  survey.applyTheme({
    ...BorderlessLight,
    cssVariables: {
      ...BorderlessLight.cssVariables,
      '--sjs-primary-backcolor': accent,
      '--sjs-primary-backcolor-dark': accent,
      '--sjs-primary-backcolor-light': `${accent}22`,
    },
  })
}

function Centered({ accent, children }: { accent: string; children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div
        className="w-full max-w-[640px] text-center bg-white rounded-2xl shadow-sm border border-slate-200 p-10"
        style={{ borderTop: `4px solid ${accent}` }}
      >
        {children}
      </div>
    </div>
  )
}

export default function RespondentPage() {
  const { token } = useParams<{ token: string }>()
  const [phase, setPhase] = useState<Phase>('loading')
  const [error, setError] = useState('')
  const [model, setModel] = useState<Model | null>(null)
  const [title, setTitle] = useState('')
  const [accent, setAccent] = useState(DEFAULT_ACCENT)
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
        // Terminal-at-entry (duplicate-pid security = 30): don't serve the survey.
        if (res.data.status_code != null) {
          terminatedRef.current = true
          setPhase('terminated')
          goRedirect(res.data.redirect_url)
          return
        }
        sessionIdRef.current = res.data.session_id
        const themeAccent = accentFor(res.data.theme)
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
        applySurveyTheme(survey, themeAccent)

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
        setAccent(themeAccent)
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
      <Centered accent={accent}>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">Something went wrong</h1>
        <p className="text-slate-600">{error || 'Please try again later.'}</p>
      </Centered>
    )
  }

  if (phase === 'closed') {
    return (
      <Centered accent={accent}>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">Survey closed</h1>
        <p className="text-slate-600">
          This survey is no longer accepting responses. Thank you for your interest.
        </p>
      </Centered>
    )
  }

  if (phase === 'welcome') {
    return (
      <Centered accent={accent}>
        <h1 className="text-2xl font-semibold text-slate-900 mb-3">{title}</h1>
        <p className="text-slate-600 mb-8">
          This survey takes about 3 minutes. Your answers are confidential.
        </p>
        <button
          onClick={() => setPhase('running')}
          className="inline-flex items-center justify-center rounded-lg px-8 py-3 text-white font-medium transition-opacity hover:opacity-90"
          style={{ backgroundColor: accent }}
        >
          Start
        </button>
      </Centered>
    )
  }

  if (phase === 'terminated') {
    return (
      <Centered accent={accent}>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">Thank you</h1>
        <p className="text-slate-600">
          Based on your answers, you do not qualify for this survey. We appreciate your time.
        </p>
      </Centered>
    )
  }

  if (phase === 'complete') {
    return (
      <Centered accent={accent}>
        <h1 className="text-2xl font-semibold text-slate-900 mb-2">Thank you!</h1>
        <p className="text-slate-600">
          Your responses have been recorded. We really appreciate you taking the time.
        </p>
      </Centered>
    )
  }

  // running — one question per page, centered card
  return (
    <div className="min-h-screen bg-slate-50 py-8">
      {/* Scoped polish: larger question type + touch-friendly, full-width options. */}
      <style>{`
        .dvv-survey .sd-question__title { font-size: 1.25rem; line-height: 1.75rem; }
        .dvv-survey .sd-item { padding: 0.75rem 1rem; border-radius: 0.5rem; }
        .dvv-survey .sd-item:hover { background: rgba(0,0,0,0.03); }
        .dvv-survey .sd-selectbase__label { width: 100%; }
      `}</style>
      <div className="dvv-survey max-w-[640px] mx-auto px-4">
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 sm:p-8">
          {model && <Survey model={model} />}
        </div>
      </div>
    </div>
  )
}
