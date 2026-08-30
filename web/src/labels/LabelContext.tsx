import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { uiApi } from '../api/client'

/**
 * Founder-editable UI copy (Tier A of the founders' console).
 *
 * Every instrumented string renders through `useLabel(key, default)`. The
 * DEFAULT passed at the call site is the real source of truth: it is what
 * ships, what renders while overrides are loading, and what renders if the
 * fetch fails, 404s, or the user is offline.
 *
 * That is the entire safety property. A founder can change any label and the
 * worst possible outcome is one word reading wrong — never a blank screen, and
 * never a crash — because the app never DEPENDS on an override existing.
 *
 * Overrides are fetched once per session. They are product copy, not live
 * state, so a change takes effect on the next load rather than streaming in
 * mid-session and shifting text under someone's cursor.
 */

interface LabelState {
  overrides: Record<string, string>
  /** Re-fetch after an edit, so the editor sees their own change immediately. */
  reload: () => void
}

const LabelContext = createContext<LabelState>({ overrides: {}, reload: () => {} })

export function LabelProvider({ children }: { children: ReactNode }) {
  const [overrides, setOverrides] = useState<Record<string, string>>({})

  const load = useCallback(() => {
    uiApi
      .overrides()
      .then((r) => setOverrides(r.data.overrides ?? {}))
      // Deliberately silent: no overrides is a completely normal state, and a
      // failure here must never surface as an error to a researcher who has no
      // idea this layer exists. The app renders its built-in copy.
      .catch(() => setOverrides({}))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const value = useMemo(() => ({ overrides, reload: load }), [overrides, load])
  return <LabelContext.Provider value={value}>{children}</LabelContext.Provider>
}

/**
 * The instrumented-string hook.
 *
 * `fallback` is required — it is the shipped wording and the guaranteed
 * render. Never call this without one.
 */
export function useLabel(key: string, fallback: string): string {
  const { overrides } = useContext(LabelContext)
  const value = overrides[key]
  return typeof value === 'string' && value.trim() ? value : fallback
}

/** Re-fetch overrides — used by the console after saving an edit. */
export function useReloadLabels(): () => void {
  return useContext(LabelContext).reload
}
