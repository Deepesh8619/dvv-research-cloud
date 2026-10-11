import { Model } from 'survey-core'
import { BorderlessLight } from 'survey-core/themes'
import type { Theme } from '../../api/themes'

// The 4px card top strip — the one deliberate flourish per the design spec.
export function gradientBar(theme: Theme): string {
  return `linear-gradient(90deg, ${theme.accent}, ${theme.accentDeep})`
}

// One polished default theme + the segment accent, applied via applyTheme. Keeps
// the modern-survey look (styled inputs, progress bar) tied to the accent; the
// deep tone drives pressed/hover, the ~10%-alpha light drives the selected tint.
// Shared by the respondent link and the editor's preview, so the preview is the
// link's look, not an imitation of it.
export function applySurveyTheme(survey: Model, theme: Theme): void {
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
