import { THEMES, THEME_SLUGS } from '../api/themes'

// Respondent-theme picker (T24): a gradient swatch of the current theme + a
// native select of the 8 theme names. Shared by the link-time picker
// (FieldingPage) and the builder picker (SurveyBuilderPage) so both read the ONE
// THEMES source of truth.
export default function ThemeSelect({
  value,
  onChange,
  disabled,
  id,
  inheritLabel,
}: {
  value: string
  onChange: (slug: string) => void
  disabled?: boolean
  id?: string
  // When set, prepends an "" option (e.g. "Survey default") meaning "don't
  // override" — used by the link picker so a builder-set default flows through.
  inheritLabel?: string
}) {
  const theme = value ? THEMES[value] ?? THEMES.indigo : null
  return (
    <div className="flex items-center gap-2">
      <span
        aria-hidden
        title={theme?.name}
        className="inline-block h-4 w-4 rounded-full border border-slate-300 shrink-0"
        style={
          theme
            ? { background: `linear-gradient(135deg, ${theme.accent}, ${theme.accentDeep})` }
            : { background: '#fff' }
        }
      />
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 px-2 rounded border border-slate-300 text-sm disabled:bg-slate-50"
      >
        {inheritLabel !== undefined && <option value="">{inheritLabel}</option>}
        {THEME_SLUGS.map((slug) => (
          <option key={slug} value={slug}>
            {THEMES[slug].name}
          </option>
        ))}
      </select>
    </div>
  )
}
