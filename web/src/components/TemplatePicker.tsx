import { themeFor } from '../api/themes'
import { TEMPLATES, TEMPLATE_SLUGS } from './respondent/templates'

// A thumbnail per respondent-link template: a tiny phone drawn in the template's
// own ground, header and option shape, in the chosen theme's colour. Shared by
// the link panel (FieldingPage) and the builder (SurveyBuilderPage).

const INK = '#1F2333'

function Thumb({ slug, theme }: { slug: string; theme: string }) {
  const t = themeFor(theme || null)
  const ground = TEMPLATES[slug].ground(t)
  const option = (on: boolean): React.CSSProperties => {
    switch (slug) {
      case 'ledger':
      case 'clear':
        return { height: 7, borderBottom: `1px solid ${slug === 'clear' ? '#111' : '#C9D0D9'}`, background: on ? `${t.accent}22` : 'transparent' }
      case 'chat':
        return { height: 7, width: on ? '55%' : '40%', marginLeft: 'auto', borderRadius: 9, border: `1px solid ${t.accent}`, background: on ? t.accent : '#fff' }
      case 'play':
        return { height: 8, borderRadius: 4, border: `1px solid ${INK}`, boxShadow: `1px 1px 0 ${INK}`, background: on ? '#FFC93C' : '#fff' }
      default:
        return { height: 8, borderRadius: slug === 'pulse' ? 4 : 3, border: `1px solid ${on ? t.accent : '#E2E8F0'}`, background: on ? `${t.accent}1A` : '#fff' }
    }
  }
  return (
    <div className="flex h-[88px] w-full flex-col overflow-hidden rounded-md" style={{ background: ground }} aria-hidden>
      {slug === 'brand' ? (
        <div className="px-2 pb-2 pt-2">
          <div className="h-1.5 w-1/2 rounded bg-white/80" />
          <div className="mt-1.5 h-0.5 rounded bg-white/25">
            <div className="h-0.5 w-1/2 rounded bg-[#E0A526]" />
          </div>
        </div>
      ) : (
        <div className="flex gap-0.5 px-2 pt-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-1 flex-1 rounded-full" style={{ background: i < 1 ? (slug === 'play' ? '#19A98A' : t.accent) : '#CBD5E1' }} />
          ))}
        </div>
      )}
      <div
        className="mx-1.5 mt-1.5 flex flex-1 flex-col gap-1 p-1.5"
        style={
          slug === 'classic' || slug === 'brand'
            ? { background: '#fff', borderRadius: slug === 'brand' ? '8px 8px 0 0' : 5 }
            : slug === 'play'
              ? { background: '#fff', border: `1px solid ${INK}`, borderRadius: 6, marginBottom: 6 }
              : {}
        }
      >
        <div
          className="mb-0.5 rounded"
          style={{
            width: slug === 'chat' ? '60%' : '80%',
            background: slug === 'chat' ? '#fff' : slug === 'ledger' ? '#13161B' : '#334155',
            height: slug === 'clear' ? 8 : 6,
          }}
        />
        {[false, true, false].map((on, i) => (
          <div key={i} style={option(on)} />
        ))}
      </div>
    </div>
  )
}

export default function TemplatePicker({
  value,
  onChange,
  theme,
  disabled,
  inheritLabel,
}: {
  value: string
  onChange: (slug: string) => void
  /** Colours the thumbnails, so the pick is seen in the study's own theme. */
  theme: string
  disabled?: boolean
  /** When set, a first tile meaning "don't override" (value ''). */
  inheritLabel?: string
}) {
  const options = inheritLabel !== undefined ? ['', ...TEMPLATE_SLUGS] : TEMPLATE_SLUGS
  return (
    <div role="radiogroup" aria-label="Link template" className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
      {options.map((slug) => {
        const on = value === slug
        return (
          <button
            key={slug || 'inherit'}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={slug ? `${TEMPLATES[slug].name}: ${TEMPLATES[slug].blurb}` : inheritLabel}
            disabled={disabled}
            onClick={() => onChange(slug)}
            className={`rounded-lg border p-1.5 text-left transition-colors disabled:opacity-50 ${
              on ? 'border-primary-500 ring-2 ring-primary-200' : 'border-slate-200 hover:border-slate-300'
            }`}
          >
            {slug ? (
              <Thumb slug={slug} theme={theme} />
            ) : (
              <div className="flex h-[88px] items-center justify-center rounded-md border border-dashed border-slate-300 text-center text-[11px] text-slate-500">
                {inheritLabel}
              </div>
            )}
            <p className="mt-1.5 text-xs font-semibold text-slate-800">{slug ? TEMPLATES[slug].name : 'Default'}</p>
            <p className="text-[11px] leading-tight text-slate-500">{slug ? TEMPLATES[slug].blurb : 'As set on the survey'}</p>
          </button>
        )
      })}
    </div>
  )
}
