import 'survey-core/defaultV2.min.css'

import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Model } from 'survey-core'
import { Survey } from 'survey-react-ui'
import { fieldingApi } from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'

// A single respondent view: fetch the compiled SurveyJS JSON for a public
// fielding token, render it, and stream answers back to the server one page at
// a time (the incremental-capture requirement). Terminates are enforced
// SERVER-SIDE: after each page POST, if the response says `terminated`, we stop
// the survey and show a screen-out — the client never decides who qualifies.
//
// Every fetch failure lands in a visible state (never a blank screen): the
// codebase had a blank-page bug fixed the day before this was built, so each of
// loading / error / closed / terminated / complete is an explicit render.

type Phase = 'loading' | 'error' | 'closed' | 'running' | 'terminated' | 'complete'

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4">
      <div className="w-full max-w-md text-center bg-white rounded-lg border border-slate-200 p-8">
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
  // Live refs the async event handlers read (state would be stale in closures).
  const sessionIdRef = useRef<string>('')
  const terminatedRef = useRef(false)

  useEffect(() => {
    let cancelled = false
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
        sessionIdRef.current = res.data.session_id
        const survey = new Model(res.data.surveyjs_json)

        // Per-page incremental capture: POST the accumulated answers whenever the
        // respondent advances a page. A terminate response stops the survey.
        survey.onCurrentPageChanged.add(async (sender) => {
          await submitPage(sender, sender.currentPageNo)
        })

        // Final page + finish: POST the last page's answers, then mark complete.
        survey.onComplete.add(async (sender) => {
          const terminated = await submitPage(sender, sender.currentPageNo)
          if (terminated || terminatedRef.current) return
          try {
            await fieldingApi.complete(token, sessionIdRef.current)
            setPhase('complete')
          } catch {
            setError('We could not save your final response. Please try again.')
            setPhase('error')
          }
        })

        setModel(survey)
        setPhase('running')
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
          setPhase('terminated')
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
      <Centered>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">Something went wrong</h1>
        <p className="text-slate-600">{error || 'Please try again later.'}</p>
      </Centered>
    )
  }

  if (phase === 'closed') {
    return (
      <Centered>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">Survey closed</h1>
        <p className="text-slate-600">
          This survey is no longer accepting responses. Thank you for your interest.
        </p>
      </Centered>
    )
  }

  if (phase === 'terminated') {
    return (
      <Centered>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">Thank you</h1>
        <p className="text-slate-600">
          Based on your answers, you do not qualify for this survey. We appreciate your time.
        </p>
      </Centered>
    )
  }

  if (phase === 'complete') {
    return (
      <Centered>
        <h1 className="text-lg font-semibold text-slate-900 mb-2">All done</h1>
        <p className="text-slate-600">Your responses have been recorded. Thank you!</p>
      </Centered>
    )
  }

  // running
  return (
    <div className="min-h-screen bg-slate-100 py-6">
      <div className="max-w-3xl mx-auto px-4">{model && <Survey model={model} />}</div>
    </div>
  )
}
