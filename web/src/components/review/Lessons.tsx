import { useEffect, useState } from 'react'
import { correctionsApi, ProfileRule } from '../../api/client'

/**
 * "Lessons this format has learned" — the active patterns bound for this
 * profile, so a reviewer can see what the parser was already taught before
 * wondering why a question wasn't read. Read-only for non-founders: they can
 * see what was taught, but only a founder may switch a lesson off (mirrors
 * the server-side gate in routes/corrections.py).
 */
interface Props {
  profileSlug: string
  profileLabel?: string | null
  canCorrect: boolean
  /** Bump after teaching a new lesson elsewhere on the page to pick it up. */
  reloadToken?: number
}

function prettifyKind(kind: string): string {
  return kind.replace(/_/g, ' ')
}

export default function Lessons({ profileSlug, profileLabel, canCorrect, reloadToken }: Props) {
  const [open, setOpen] = useState(false)
  const [rules, setRules] = useState<ProfileRule[]>([])
  const [loaded, setLoaded] = useState(false)

  const load = () =>
    correctionsApi
      .rules(profileSlug)
      .then((r) => setRules(r.data.rules))
      .catch(() => setRules([]))
      .finally(() => setLoaded(true))

  useEffect(() => {
    if (open) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, profileSlug, reloadToken])

  // A free-text correction with no pattern_kind is prose written for a
  // person, not something the deterministic parsers read — only the
  // machine-applicable half counts as "a lesson this format has learned".
  const patterns = rules.filter((r) => r.pattern_kind)
  const active = patterns.filter((r) => r.active).length

  const toggle = async (rule: ProfileRule) => {
    await correctionsApi.toggleRule(profileSlug, rule.id).catch(() => undefined)
    load()
  }

  return (
    <section className="rounded-md border border-slate-200">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center justify-between px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 hover:bg-slate-50"
      >
        <span>
          Lessons this format has learned
          {loaded && ` (${active} active)`}
        </span>
        <span className={`text-slate-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}>
          ▾
        </span>
      </button>
      {open && (
        <div className="px-3 pb-3">
          {patterns.length === 0 ? (
            <p className="text-xs text-slate-400">
              Nothing taught yet for {profileLabel || profileSlug}.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {patterns.map((r) => (
                <li key={r.id} className="flex items-start gap-2 text-xs">
                  {canCorrect ? (
                    <button
                      type="button"
                      onClick={() => toggle(r)}
                      title={r.active ? 'Switch this lesson off' : 'Switch this lesson back on'}
                      className={`shrink-0 mt-0.5 w-8 text-[9px] font-mono uppercase rounded px-1 py-0.5 border transition ${
                        r.active
                          ? 'bg-emerald-50 border-emerald-300 text-emerald-800'
                          : 'bg-slate-100 border-slate-300 text-slate-400'
                      }`}
                    >
                      {r.active ? 'on' : 'off'}
                    </button>
                  ) : (
                    <span
                      className={`shrink-0 mt-0.5 w-8 text-[9px] font-mono uppercase rounded px-1 py-0.5 border text-center ${
                        r.active
                          ? 'bg-emerald-50 border-emerald-300 text-emerald-800'
                          : 'bg-slate-100 border-slate-300 text-slate-400'
                      }`}
                    >
                      {r.active ? 'on' : 'off'}
                    </span>
                  )}
                  <span className={r.active ? 'text-slate-700' : 'text-slate-400 line-through'}>
                    <span className="font-medium">{prettifyKind(r.pattern_kind!)}</span>
                    {' — '}
                    <span className="font-mono">{r.pattern}</span>
                    {r.pattern_value ? (
                      <>
                        {' → '}
                        <span className="font-mono">{r.pattern_value}</span>
                      </>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
