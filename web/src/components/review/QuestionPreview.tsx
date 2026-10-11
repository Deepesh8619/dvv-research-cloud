import 'survey-core/defaultV2.min.css'
import { useEffect, useMemo, useState } from 'react'
import { Model } from 'survey-core'
import { Survey } from 'survey-react-ui'
import { questionnaireApi } from '../../api/client'
import { themeFor } from '../../api/themes'
import { registerRespondentControls } from '../respondent/controls'
import { applySurveyTheme, gradientBar } from '../respondent/surveyLook'

registerRespondentControls()

/**
 * The question as the survey link will show it — while it is being edited.
 *
 * Built by the link's own compiler from the editor's unsaved state, drawn by
 * the same renderer, theme and custom controls, so what is seen here is what a
 * respondent gets — not an imitation that drifts from it. Answers given here
 * go nowhere; "Check my answers" runs the link's own rules on them.
 */
export default function QuestionPreview({
  studyId,
  qid,
  current,
  version,
}: {
  studyId: string
  qid: string
  /** The editor's question, unsaved. */
  current: () => Record<string, unknown>
  /** Changes whenever the editor does — refetches after a short pause. */
  version: string
}) {
  const [json, setJson] = useState<Record<string, unknown> | null>(null)
  const [themeSlug, setThemeSlug] = useState<string | null>(null)
  const [notes, setNotes] = useState<string[]>([])
  const [problems, setProblems] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [device, setDevice] = useState<'mobile' | 'desktop'>('mobile')
  const [check, setCheck] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const timer = window.setTimeout(async () => {
      try {
        const r = await questionnaireApi.preview(studyId, qid, current())
        if (cancelled) return
        setJson(r.data.survey_json)
        setNotes(r.data.notes)
        setProblems(r.data.problems ?? [])
        setThemeSlug(r.data.theme ?? null)
        setError(null)
        setCheck(null)
      } catch {
        if (!cancelled) setError('The preview could not be built.')
      }
    }, 450)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
    // `current` is a fresh closure every render; `version` is what changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studyId, qid, version])

  const theme = themeFor(themeSlug)
  const model = useMemo(() => {
    if (!json) return null
    const survey = new Model(json)
    applySurveyTheme(survey, theme)
    return survey
  }, [json, theme])

  return (
    <details open className="border border-slate-200 rounded-md bg-slate-50">
      <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-slate-800 flex items-center gap-2">
        Preview in the link
        <span className="font-normal text-xs text-slate-500">updates as you edit · nothing is saved</span>
      </summary>
      <div className="px-3 pb-3 space-y-2">
        <div className="flex items-center gap-1.5">
          {(['mobile', 'desktop'] as const).map((d) => (
            <button
              key={d}
              onClick={() => setDevice(d)}
              className={`text-xs px-2.5 py-1 rounded-full border ${
                device === d ? 'border-slate-500 bg-white font-semibold' : 'border-slate-200 text-slate-500'
              }`}
            >
              {d === 'mobile' ? 'Phone' : 'Desktop'}
            </button>
          ))}
          {model && (
            <button
              onClick={() => {
                const ok = model.validateCurrentPage()
                setCheck(ok ? 'These answers pass every rule this question has.' : null)
              }}
              className="ml-auto text-xs px-2.5 py-1 rounded border border-slate-300 bg-white hover:bg-slate-50"
            >
              Check my answers
            </button>
          )}
        </div>
        {error && <p className="text-sm text-rose-700">{error}</p>}
        {problems.length > 0 && (
          <div className="text-xs text-rose-800 bg-rose-50 border border-rose-200 rounded px-2 py-1.5">
            <span className="font-semibold">QA would flag this:</span>
            <ul className="list-disc pl-4 mt-0.5">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </div>
        )}
        {notes.map((n) => (
          <p key={n} className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1">
            {n}
          </p>
        ))}
        {check && <p className="text-xs text-emerald-700">{check}</p>}
        <div className="flex justify-center rounded-md py-4 px-2" style={{ background: theme.wash }}>
          <div
            className="bg-white rounded-2xl shadow-md border border-slate-200/80 overflow-hidden transition-all"
            style={{ width: device === 'mobile' ? 375 : '100%', maxWidth: 640 }}
            data-testid="question-preview"
          >
            <div style={{ height: 4, background: gradientBar(theme) }} />
            {/* A screen, like the respondent's: a long question scrolls inside
                it instead of stretching the whole editor. */}
            <div className="overflow-y-auto" style={{ maxHeight: device === 'mobile' ? 600 : 520 }}>
              {model ? <Survey model={model} /> : <div className="p-6 text-sm text-slate-400">Building preview…</div>}
            </div>
          </div>
        </div>
      </div>
    </details>
  )
}
