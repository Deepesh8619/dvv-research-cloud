import 'survey-core/defaultV2.min.css'

import { useEffect, useMemo, useState } from 'react'
import { Model } from 'survey-core'
import { Survey } from 'survey-react-ui'
import { Question } from '../../api/client'
import { registerRespondentControls } from '../respondent/controls'
import { buildPreviewJson } from './preview'

// Grid/rank/number questions get our own respondent controls, not SurveyJS's
// stock ones (see components/respondent/controls.tsx) — same registration the
// real fielding page runs, so what a researcher previews here is what a
// respondent actually sees, not a stand-in.
registerRespondentControls()

/** Waits for typing to pause before rebuilding the SurveyJS model. Rebuilding
    on every keystroke would replace the DOM node under the researcher's
    cursor mid-word — this is the "debounced" from the spec, in one hook. */
function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return debounced
}

/** Renders exactly what a respondent would see for the given question(s) —
    one question for the per-question preview, the whole draft for the
    survey-wide toggle. Read-only: nothing here is ever submitted. */
export function RespondentPreview({
  questions,
  debounceMs = 350,
}: {
  questions: Question[]
  debounceMs?: number
}) {
  const debounced = useDebounced(questions, debounceMs)
  const model = useMemo(() => {
    if (debounced.length === 0) return null
    try {
      return new Model(buildPreviewJson(debounced))
    } catch {
      // A half-edited question (e.g. a grid mid-type-change with no rows yet)
      // should not crash the pane — it should just show nothing until it's
      // valid again.
      return null
    }
  }, [debounced])

  if (!model) {
    return <p className="text-sm text-slate-400">Nothing to preview yet.</p>
  }
  return (
    <div aria-label="Respondent preview" className="dvv-pop">
      <Survey model={model} />
    </div>
  )
}
