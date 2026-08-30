import { useCallback, useEffect, useMemo, useState } from 'react'
import { activityApi, uiApi, type UIKey } from '../api/client'
import { useReloadLabels } from '../labels/LabelContext'
import { CardListSkeleton } from '../components/Skeleton'

/**
 * The founders' wording console (Tier A).
 *
 * Vaibhav and Vishal know what an MR team actually calls things; the app ships
 * my guesses. This makes changing that a text box instead of a deploy.
 *
 * Two deliberate constraints, both about keeping the blast radius at "a word
 * reads wrong":
 *  - only keys declared in the server's UI_KEYS registry appear, so copy can
 *    never be written into a key nothing renders;
 *  - the built-in wording is always shown beside the edit, and Revert is one
 *    click, so there is no state a founder can get into that they cannot undo.
 */

export default function WordingPage() {
  const [keys, setKeys] = useState<UIKey[] | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const reloadLabels = useReloadLabels()


  const load = useCallback(() => {
    uiApi
      .keys()
      .then((r) => setKeys(r.data.keys))
      .catch(() => setError('Could not load the wording list.'))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const grouped = useMemo(() => {
    const match = filter.trim().toLowerCase()
    const rows = (keys ?? []).filter(
      (k) =>
        !match ||
        k.current.toLowerCase().includes(match) ||
        k.default.toLowerCase().includes(match) ||
        k.where.toLowerCase().includes(match),
    )
    const out = new Map<string, UIKey[]>()
    for (const k of rows) {
      const list = out.get(k.where) ?? []
      list.push(k)
      out.set(k.where, list)
    }
    return [...out.entries()]
  }, [keys, filter])

  // Record what they SEARCHED FOR, debounced so a keystroke isn't an event.
  // Fire-and-forget: telemetry must never delay or break the screen it watches.
  useEffect(() => {
    const q = filter.trim()
    if (q.length < 3) return
    const t = window.setTimeout(() => {
      activityApi.record({
        action: 'searched',
        surface: 'wording',
        detail: q,
        found: grouped.reduce((n, [, rows]) => n + rows.length, 0),
      })
    }, 900)
    return () => window.clearTimeout(t)
  }, [filter, grouped])

  const save = async (k: UIKey) => {
    const value = (drafts[k.key] ?? k.current).trim()
    if (!value || value === k.current) return
    setBusy(k.key)
    setError(null)
    try {
      await uiApi.set(k.key, value)
      setSaved(k.key)
      setDrafts((d) => {
        const next = { ...d }
        delete next[k.key]
        return next
      })
      load()
      reloadLabels()
      window.setTimeout(() => setSaved(null), 2500)
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Could not save that wording.')
    } finally {
      setBusy(null)
    }
  }

  const revert = async (k: UIKey) => {
    setBusy(k.key)
    setError(null)
    try {
      await uiApi.revert(k.key)
      setDrafts((d) => {
        const next = { ...d }
        delete next[k.key]
        return next
      })
      load()
      reloadLabels()
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail ?? 'Could not revert that wording.')
    } finally {
      setBusy(null)
    }
  }

  const overriddenCount = (keys ?? []).filter((k) => k.overridden).length

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-semibold text-slate-900">Wording</h1>
      <p className="text-sm text-slate-600 mt-1 max-w-2xl leading-relaxed">
        Change what the app calls things, in your language rather than ours. Edits take effect
        on the next page load and can always be put back.
      </p>

      <div className="flex items-center gap-3 mt-5 mb-4">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search wording…"
          aria-label="Search wording"
          className="text-sm rounded border border-slate-300 px-3 py-1.5 w-72 bg-white
                     focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
        />
        {overriddenCount > 0 && (
          <span className="text-xs text-slate-500 font-mono tabular-nums">
            {overriddenCount} changed
          </span>
        )}
      </div>

      {error && (
        <p className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-3 mb-4">
          {error}
        </p>
      )}

      {keys === null ? (
        <CardListSkeleton />
      ) : (
        grouped.map(([where, rows]) => (
          <section key={where} className="mb-7">
            <h2 className="text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-2">
              {where}
            </h2>
            <div className="border-t border-slate-200">
              {rows.map((k) => {
                const draft = drafts[k.key] ?? k.current
                const dirty = draft.trim() !== k.current
                return (
                  <div key={k.key} className="border-b border-slate-200 py-3">
                    <div className="flex items-baseline justify-between gap-3 mb-1.5">
                      <span className="text-xs text-slate-600">{k.note}</span>
                      {k.overridden && (
                        <span className="text-[9px] font-mono uppercase tracking-wide px-1.5 py-0.5
                                         rounded bg-sky-50 text-sky-700 border border-sky-200">
                          changed
                        </span>
                      )}
                    </div>

                    <div className="flex flex-wrap items-start gap-2">
                      <textarea
                        value={draft}
                        onChange={(e) =>
                          setDrafts((d) => ({ ...d, [k.key]: e.target.value }))
                        }
                        rows={draft.length > 90 ? 3 : 1}
                        aria-label={k.note}
                        className="flex-1 min-w-[18rem] text-sm rounded border border-slate-300
                                   px-3 py-1.5 bg-white resize-y
                                   focus:outline-none focus:ring-2 focus:ring-sky-500
                                   focus:border-sky-500"
                      />
                      <button
                        onClick={() => save(k)}
                        disabled={!dirty || busy === k.key}
                        className="text-sm font-medium bg-slate-900 text-white px-3 py-1.5 rounded
                                   hover:bg-slate-700 disabled:opacity-30
                                   disabled:cursor-not-allowed active:scale-[0.98] transition
                                   focus-visible:outline-none focus-visible:ring-2
                                   focus-visible:ring-sky-500"
                      >
                        {busy === k.key ? 'Saving…' : saved === k.key ? 'Saved' : 'Save'}
                      </button>
                      {k.overridden && (
                        <button
                          onClick={() => revert(k)}
                          disabled={busy === k.key}
                          title={`Restore: ${k.default}`}
                          className="text-sm text-slate-600 px-3 py-1.5 rounded border
                                     border-slate-300 hover:bg-slate-50 active:scale-[0.98]
                                     transition focus-visible:outline-none
                                     focus-visible:ring-2 focus-visible:ring-sky-500"
                        >
                          Revert
                        </button>
                      )}
                    </div>

                    {/* The shipped wording stays visible, so there is always a
                        known-good value to go back to. */}
                    {k.overridden && (
                      <p className="text-xs text-slate-400 mt-1.5">
                        Ours: <span className="text-slate-500">{k.default}</span>
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          </section>
        ))
      )}

      {keys !== null && grouped.length === 0 && (
        <p className="text-sm text-slate-500">Nothing matches “{filter}”.</p>
      )}
    </div>
  )
}
