import 'survey-core/defaultV2.min.css'
import QuestionSymbol from '../components/respondent/symbols'

import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Model } from 'survey-core'
import { BorderlessLight } from 'survey-core/themes'
import { Survey } from 'survey-react-ui'
import { GridMaskSource, PipeVariable, errorMessage, fieldingApi } from '../api/client'
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
import { registerRespondentControls } from '../components/respondent/controls'

// Grid, rank and number questions are drawn by us — see controls.tsx.
registerRespondentControls()

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

// Where the respondent is, from the survey's OWN counters. `currentPageNo` does
// not move in questionPerPage mode — SurveyJS swaps the content of one page — so
// reading it left the header frozen at "1 / 20" for the whole interview. And its
// question count is the real one: getAllQuestions() included an element nobody
// answers, which is how the total read 20 against the survey's own 19.
// What a respondent may read as the survey's name. A title that is really the
// uploaded FILE ("02QRE_FINAL_-_Bubble_Consumer_Research_5.6.26") is scripter
// vocabulary, and it names the sponsor — which a blind study must never show.
export function respondentTitle(title: string | undefined): string {
  const t = (title || '').trim()
  if (!t || /_|\.(docx?|pdf)$|\bQRE\b|\bFINAL\b|\bv\d+\b|\d+\.\d+\.\d+/i.test(t)) return ''
  return t
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

/** One place in a ranking, as the label the respondent read.
 *
 * SurveyJS prints a whole ranking for {Q7} and cannot address one position, so
 * a question about "your top choice" named every brand they ranked. The
 * compiler asks for `{Q7_rank1}`; this fills it in as they answer.
 */
/** The rows of a grid the respondent "picked", as a plain list of codes.
 *
 * A mask sourced from a grid ("only the pharmacies they have heard of") has
 * no expression SurveyJS can evaluate: a grid answer is {row: column} and
 * there is no way to say "the rows whose cell says Yes". Before this, such a
 * mask compiled to `{Q5} = {item}` — an object against a code, never true —
 * and the masked question offered NOTHING. The server says which columns are
 * affirmative; this publishes the picked rows for `contains` to test.
 */
export function applyGridMaskVariables(survey: Model, sources: GridMaskSource[]): void {
  for (const source of sources) {
    const answer = survey.getQuestionByName(source.qid)?.value as
      | Record<string, unknown>
      | undefined
    const positive = new Set(source.positive.map(String))
    const picked: number[] = []
    for (const [row, value] of Object.entries(answer ?? {})) {
      const chosen = Array.isArray(value) ? value : [value]
      if (chosen.some((v) => positive.has(String(v)))) picked.push(Number(row))
    }
    survey.setVariable(source.name, picked)
  }
}

export function applyPipeVariables(survey: Model, variables: PipeVariable[]): void {
  for (const v of variables) {
    const question = survey.getQuestionByName(v.qid)
    const ranked = (question?.value as unknown[]) ?? []
    const code = v.position < 0 ? ranked[ranked.length - 1] : ranked[v.position - 1]
    const choices = (question as unknown as { visibleChoices?: Array<{ value: unknown; text: string }> })
      ?.visibleChoices
    const label = choices?.find((c) => String(c.value) === String(code))?.text
    survey.setVariable(v.name, label ?? '')
  }
}

function positionOf(survey: Model): { at: number; of: number; qid: string } {
  const info = survey.getProgressInfo()
  const of = info.questionCount || 1
  // The question ON SCREEN. Counting answers instead said "2 of 7" the moment
  // one statement of a six-row grid was answered, while still on question 1.
  const current = survey.currentSingleQuestion
  const answerable = survey.getAllQuestions(true).filter((q) => q.getType() !== 'html')
  const qid = current?.name ?? ''
  const index = current ? answerable.indexOf(current) : -1
  if (index >= 0) return { at: Math.min(index + 1, of), of, qid }
  return { at: Math.min(info.answeredQuestionCount + 1, of), of, qid }
}

export default function RespondentPage() {
  const { token } = useParams<{ token: string }>()
  const [phase, setPhase] = useState<Phase>('loading')
  const [error, setError] = useState('')
  const [model, setModel] = useState<Model | null>(null)
  const [title, setTitle] = useState('')
  const [theme, setTheme] = useState<Theme>(DEFAULT_THEME)
  // Where the respondent is, kept in state because SurveyJS mutates its model
  // in place: reading currentPageNo during render shows the page they were on
  // last time React happened to draw.
  const [position, setPosition] = useState({ at: 1, of: 1, qid: '' })
  // {qid: symbol name}, read from the questionnaire's own wording on the server.
  const [symbols, setSymbols] = useState<Record<string, string>>({})
  // The blocks the script was written in: the progress bar's segments.
  const [sections, setSections] = useState(1)
  // Live refs the async event handlers read (state would be stale in closures).
  const sessionIdRef = useRef<string>('')
  const terminatedRef = useRef(false)
  // Test-link jump-to-question (see api/routes/fielding.py jump_to_question).
  const [isTest, setIsTest] = useState(false)
  const [jumping, setJumping] = useState(false)
  const [jumpNote, setJumpNote] = useState<string | null>(null)
  const [jumpError, setJumpError] = useState<string | null>(null)

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
        setIsTest(!!res.data.is_test)
        const surveyTheme = themeFor(res.data.theme)
        const survey = new Model(res.data.surveyjs_json)
        const pipeVariables = (res.data.surveyjs_json as { pipeVariables?: PipeVariable[] })
          .pipeVariables ?? []
        setSymbols(
          (res.data.surveyjs_json as { questionSymbols?: Record<string, string> })
            .questionSymbols ?? {},
        )
        const gridMasks = (res.data.surveyjs_json as { gridMaskSources?: GridMaskSource[] })
          .gridMaskSources ?? []

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
        // Rankings are tapped into order, which builds the answer one item at a
        // time — the model's select-to-rank mode is the one that holds a
        // partial ranking without filling in the rest.
        for (const q of survey.getAllQuestions()) {
          if (q.getType() === 'ranking') (q as unknown as { selectToRankEnabled: boolean }).selectToRankEnabled = true
          // A stepper writes on every tap; auto-advance would leave after the
          // first "+" and nobody could ever enter 2.
          // Constant-sum boxes too: filling the last box finished the survey
          // before anyone could check the numbers.
          const typed =
            q.getType() === 'multipletext' ||
            (q.getType() === 'text' && (q as unknown as { inputType: string }).inputType === 'number')
          if (typed) {
            ;(q as unknown as { supportGoNextPageAutomatic: () => boolean }).supportGoNextPageAutomatic = () => false
          }
        }
        // A required ranking is a full ranking: stopping at #2 of 7 leaves the
        // data file with five blanks the analyst cannot tell from "skipped".
        survey.onValidateQuestion.add((_, options) => {
          const q = options.question
          if (q.getType() !== 'ranking' || !q.isRequired) return
          const total = (q as unknown as { visibleChoices: unknown[] }).visibleChoices.length
          const done = Array.isArray(options.value) ? options.value.length : 0
          if (done > 0 && done < total) options.error = `Please rank all ${total} — ${total - done} left.`
        })
        setSections(Math.max(1, survey.visiblePages.length))
        setPosition(positionOf(survey))

        // Per-page incremental capture: POST accumulated answers on every advance.
        // Multi/checkbox/text don't auto-advance — their Next click fires this too.
        survey.onCurrentPageChanged.add(async (sender) => {
          setPosition(positionOf(sender))
          await submitPage(sender, sender.currentPageNo)
        })

        // A multi-punch or text answer changes the count without changing page.
        survey.onValueChanged.add((sender) => {
          setPosition(positionOf(sender))
          applyPipeVariables(sender, pipeVariables)
          applyGridMaskVariables(sender, gridMasks)
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

        // Dev builds only: the live model, for inspecting it from the console.
        if (import.meta.env.DEV) (window as unknown as { __dvvSurvey: Model }).__dvvSurvey = survey
        applyPipeVariables(survey, pipeVariables)
        applyGridMaskVariables(survey, gridMasks)
        setModel(survey)
        setTitle(respondentTitle(survey.title))
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

  // Jump straight to a question: the server fills in every answer a
  // qualifying respondent would have given on the way there (screeners
  // passed, bases met), we push those onto the live model, then land on it.
  const handleJump = async (qid: string) => {
    if (!qid || !token || !model) return
    setJumping(true)
    setJumpError(null)
    setJumpNote(null)
    try {
      const res = await fieldingApi.jump(token, sessionIdRef.current, qid)
      const { answers, kept_your_answers } = res.data
      for (const [k, v] of Object.entries(answers)) model.setValue(k, v)
      const target = model.getQuestionByName(qid)
      if (target) model.currentSingleQuestion = target
      setPosition(positionOf(model))
      const n = Object.keys(answers).length
      setJumpNote(
        `Filled ${n} earlier answer${n === 1 ? '' : 's'} so every condition before ${qid} is met` +
          (kept_your_answers
            ? ''
            : ' — your earlier answers could not reach it, so a fresh path was used'),
      )
    } catch (err) {
      setJumpError(errorMessage(err) || `Could not jump to ${qid}`)
    } finally {
      setJumping(false)
    }
  }

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
        <h1 className="text-2xl font-semibold text-slate-900 mb-3">{title || 'Welcome'}</h1>
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

  // running — one question per page on ONE surface.
  //
  // SurveyJS's default chrome nests a page panel inside a question panel inside
  // the card: three borders around a single question, with the BLOCK's internal
  // name ("Main") printed above it as a heading. On a phone that pushed the
  // first option below the fold and showed a respondent a word from the
  // scripter's vocabulary. Everything below flattens that to one surface and
  // spends the height it frees on the answers.
  const progress = Math.min(100, Math.round((position.at / Math.max(position.of, 1)) * 100))
  const segments = sections >= 2 && sections <= 8 ? sections : Math.min(5, Math.max(position.of, 1))

  return (
    <div className="min-h-screen pb-32" style={{ background: theme.wash }}>
      <style>{`
        /* One surface: no panel inside a panel inside a card. */
        .dvv-survey .sd-root-modern, .dvv-survey .sd-container-modern,
        .dvv-survey .sd-page, .dvv-survey .sd-body { background: transparent; }
        .dvv-survey .sd-page { padding: 0; }
        .dvv-survey .sd-question { box-shadow: none; background: transparent; padding: 0; }
        /* "Main" is a block name from the script, never respondent-facing, and
           the study title already sits in the header above where it costs no
           height on a phone. The survey HEADER only: sd-title is also the class
           on every question title, so hiding that took the question with it and
           left a page of unlabelled options. */
        .dvv-survey .sd-page__title, .dvv-survey .sd-page__description,
        .dvv-survey .sd-container-modern__title, .dvv-survey .sd-header { display: none; }
        /* Our own progress lives in the header; SurveyJS's duplicates it. */
        .dvv-survey .sd-progress, .dvv-survey .sd-body__progress { display: none; }

        .dvv-survey .sd-question__title {
          font-size: 1.375rem; line-height: 1.85rem; font-weight: 600;
          color: #0f172a; letter-spacing: -0.01em; margin-bottom: 1.25rem;
        }
        .dvv-survey .sd-question__required-text { color: ${theme.accent}; }

        /* Every option is its own row you can hit with a thumb: a bordered card,
           48px minimum, the whole row clickable — not a 20px circle with a label
           sitting somewhere to its right. */
        .dvv-survey .sd-item {
          width: 100%;
          min-height: 52px; padding: 0.875rem 1rem; margin-bottom: 0.625rem;
          border: 1.5px solid #e2e8f0; border-radius: 0.75rem; background: #fff;
          cursor: pointer;
          transition: border-color 160ms ease-out, background 160ms ease-out,
                      box-shadow 160ms ease-out, transform 120ms ease-out;
        }
        .dvv-survey .sd-item:hover { border-color: ${theme.accent}80; background: ${theme.accent}08; }
        .dvv-survey .sd-item:active { transform: scale(0.995); }
        .dvv-survey .sd-item--checked {
          border-color: ${theme.accent}; background: ${theme.accent}12;
          box-shadow: 0 0 0 1px ${theme.accent};
        }
        .dvv-survey .sd-item__control-label { font-size: 1rem; color: #1e293b; }
        /* The whole row is the target; the control keeps its own size. */
        .dvv-survey .sd-selectbase__label { display: flex; align-items: center; width: 100%; gap: 0.75rem; }
        .dvv-survey .sd-item__decorator { flex: 0 0 auto; }
        .dvv-survey .sd-item__control-label { flex: 1 1 auto; }

        /* Text and numeric answers get the same card treatment. */
        .dvv-survey .sd-input {
          min-height: 52px; font-size: 1rem; border-radius: 0.75rem;
          border: 1.5px solid #e2e8f0; padding: 0.875rem 1rem; background: #fff;
        }
        .dvv-survey .sd-input:focus { border-color: ${theme.accent}; box-shadow: 0 0 0 3px ${theme.accent}26; outline: none; }

        /* The actions float in a dark bar at the bottom, where the thumb is —
           always in the same place, whatever the length of the question. */
        .dvv-survey .sd-action-bar.sd-body__navigation {
          position: fixed; left: 50%; bottom: 14px; transform: translateX(-50%);
          width: min(608px, calc(100% - 24px)); z-index: 30; margin: 0;
          /* !important: SurveyJS's responsive body rule zeroes three sides. */
          display: flex; gap: 8px; padding: 8px !important; border-radius: 20px; background: #111827;
          box-shadow: 0 12px 32px -8px rgba(15,23,42,0.45);
        }
        .dvv-survey .sd-body__navigation .sv-action { flex: 1 1 0; margin: 0; }
        .dvv-survey .sd-body__navigation .sv-action__content { width: 100%; }
        .dvv-survey .sd-body__navigation .sd-btn { width: 100%; min-height: 48px; border-radius: 14px; margin: 0; }
        .dvv-survey .sd-body__navigation .sd-navigation__prev-btn {
          background: transparent; color: #e2e8f0; border: 1.5px solid #374151;
        }
        /* SurveyJS pads its body for its own chrome; ours is the card. */
        .dvv-survey .sd-body.sd-body--responsive, .dvv-survey .sd-body--responsive .sd-page { padding: 0; }
        /* Grid questions inherit nowrap from SurveyJS's table layout, which
           cut a long statement off mid-word. */
        .dvv-survey .dvv-carousel { white-space: normal; }
        /* The root scrolls sideways by default, and the floating bar counted as
           overflow — a stray scrollbar under every question on a phone. */
        .dvv-survey .sd-root-modern { overflow: visible; }
        /* A rule under the question title, from SurveyJS's grid layout. */
        .dvv-survey .sd-question__header::after { display: none; }
        /* The error sits inside the card, not bled past its edge. */
        .dvv-survey .sd-question__erbox {
          margin: 0 0 12px; padding: 10px 14px; border-radius: 12px; font-size: 0.875rem;
        }

        /* A grid statement or a ranked item slides in rather than appearing. */
        .dvv-survey .dvv-slide { animation: dvv-in 240ms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
        @keyframes dvv-in {
          from { opacity: 0; transform: translateX(14px); }
          to   { opacity: 1; transform: none; }
        }

        /* One solid action, full width on a phone where the thumb is. */
        .dvv-survey .sd-btn, .dvv-survey .sd-navigation__next-btn,
        .dvv-survey .sd-navigation__complete-btn {
          background: ${theme.accent}; color: #fff; border: none;
          min-height: 52px; border-radius: 0.75rem; font-size: 1rem; font-weight: 600;
          box-shadow: 0 1px 2px rgba(15,23,42,0.08);
          transition: filter 160ms ease-out, transform 120ms ease-out;
        }
        .dvv-survey .sd-btn:hover { filter: brightness(0.94); }
        .dvv-survey .sd-btn:active { transform: translateY(1px); }
        .dvv-survey .sd-navigation__prev-btn {
          background: #fff; color: #475569; border: 1.5px solid #e2e8f0; box-shadow: none;
        }
        .dvv-survey .sd-action-bar { padding: 0; gap: 0.75rem; }

        /* The question arrives rather than appearing. Short enough that a fast
           respondent never waits on it. */
        .dvv-survey .sd-question { animation: dvv-rise 220ms ease-out both; }
        @keyframes dvv-rise {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: none; }
        }
        @media (prefers-reduced-motion: reduce) {
          .dvv-survey .sd-question, .dvv-survey .dvv-slide { animation: none; }
          .dvv-survey .sd-item, .dvv-survey .sd-btn { transition: none; }
          .dvv-progress__fill { transition: none; }
        }
      `}</style>

      {isTest && (
        <div className="sticky top-0 z-30 bg-amber-100 border-b border-amber-300 px-4 py-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-amber-900">
          <span className="font-semibold">Test link — answers are not real data</span>
          <select
            value=""
            disabled={jumping || !model}
            onChange={(e) => {
              if (e.target.value) void handleJump(e.target.value)
            }}
            className="h-6 px-1.5 rounded border border-amber-300 bg-white text-xs text-amber-900"
            aria-label="Jump to question"
          >
            <option value="">{jumping ? 'Jumping…' : 'Jump to question…'}</option>
            {model?.getAllQuestions().map((q) => (
              <option key={q.name} value={q.name}>
                {q.name} — {truncate(String(q.title || q.name), 60)}
              </option>
            ))}
          </select>
          {jumpNote && <span className="text-emerald-700">{jumpNote}</span>}
          {jumpError && <span className="text-red-700">{jumpError}</span>}
        </div>
      )}

      {/* Progress first: how much is left is the single thing a respondent most
          wants to know, and the commonest reason they abandon. */}
      <div className="sticky top-0 z-20 pt-3" style={{ background: theme.wash }}>
        {/* Segments, not one hairline: a finished chunk reads as a milestone,
            which is what keeps someone going through a long questionnaire. The
            script's own sections when it has several, otherwise five steps. */}
        <div className="max-w-[640px] mx-auto px-4 sm:px-0 flex gap-1.5" aria-hidden>
          {Array.from({ length: segments }, (_, i) => (
            <div key={i} className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200/80">
              <div
                className="dvv-progress__fill h-full rounded-full"
                style={{
                  width: `${Math.max(0, Math.min(1, (progress / 100) * segments - i)) * 100}%`,
                  background: theme.accent,
                  transition: 'width 280ms cubic-bezier(0.4, 0, 0.2, 1)',
                }}
              />
            </div>
          ))}
        </div>
        <div className="max-w-[640px] mx-auto px-4 sm:px-0 flex items-center justify-between py-3">
          <span className="text-sm font-medium text-slate-500 truncate pr-3">{title}</span>
          <span className="text-sm font-semibold text-slate-400 tabular-nums shrink-0">
            {position.at} / {position.of}
          </span>
        </div>
      </div>

      <div className="dvv-survey max-w-[640px] mx-auto px-4 sm:px-0 pt-2">
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200/70 p-5 sm:p-8">
          <QuestionSymbol name={symbols[position.qid]} accent={theme.accent} />
          {model && <Survey model={model} />}
        </div>
        <p className="mt-6 text-center text-xs font-medium tracking-wide text-slate-400">
          Powered by DVV Research Cloud
        </p>
      </div>
    </div>
  )
}
