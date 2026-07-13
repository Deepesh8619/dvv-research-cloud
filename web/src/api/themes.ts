// Respondent theme gallery (T24) — the ONE source of truth shared by the
// respondent runtime (RespondentPage), the link-time picker (FieldingPage), and
// the builder picker (SurveyBuilderPage). Slugs mirror backend THEME_SLUGS in
// src/core/survey_definition.py; keep the two lists in sync.
//
// Per theme: `accent` (primary buttons, progress fill, selected-option tint),
// `accentDeep` (hover/pressed + the card gradient-bar end), `wash` (page
// background), `name` (human label for pickers). Themes recolor only — layout,
// typography and spacing come from the universal card and stay unchanged.

export interface Theme {
  accent: string
  accentDeep: string
  wash: string
  name: string
}

export const THEMES: Record<string, Theme> = {
  fmcg: { accent: '#1E9E63', accentDeep: '#14532D', wash: '#F4FAF6', name: 'Fresh Mint' },
  healthcare: { accent: '#2274C4', accentDeep: '#123C63', wash: '#F3F8FC', name: 'Clinical Calm' },
  bfsi: { accent: '#1B3A6B', accentDeep: '#0E1F3A', wash: '#F5F6F9', name: 'Trust Navy' },
  auto: { accent: '#C8302E', accentDeep: '#17181A', wash: '#F6F6F5', name: 'Graphite Drive' },
  telecom: { accent: '#7C3AED', accentDeep: '#4C1D95', wash: '#F7F5FC', name: 'Signal Violet' },
  retail: { accent: '#E8730C', accentDeep: '#7C3A00', wash: '#FCF7F1', name: 'Bazaar Warm' },
  indigo: { accent: '#4F46E5', accentDeep: '#312E81', wash: '#F6F6FA', name: 'DVV Indigo' },
  slate: { accent: '#334155', accentDeep: '#0F172A', wash: '#F5F6F7', name: 'Studio Slate' },
}

// Default when a survey's theme is null/unknown.
export const DEFAULT_THEME_SLUG = 'indigo'

export function themeFor(slug?: string | null): Theme {
  return (slug && THEMES[slug]) || THEMES[DEFAULT_THEME_SLUG]
}

// Stable order for pickers (default first).
export const THEME_SLUGS = [
  'indigo',
  'fmcg',
  'healthcare',
  'bfsi',
  'auto',
  'telecom',
  'retail',
  'slate',
] as const
