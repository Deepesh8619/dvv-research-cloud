import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
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
    // Only fetch when signed in. This provider wraps the WHOLE app, login page
    // included, and /ui/overrides requires auth — so fetching without a token
    // returned 401, which the client's response interceptor turns into
    // `window.location.href = '/login'`. On the login page that is a full
    // reload, which remounts this provider, which fetches again: an infinite
    // reload loop that made signing in impossible.
    if (!localStorage.getItem('authToken')) {
      setOverrides({})
      return
    }
    uiApi
      .overrides()
      .then((r) => setOverrides(r.data.overrides ?? {}))
      // Deliberately silent: no overrides is a completely normal state, and a
      // failure here must never surface as an error to a researcher who has no
      // idea this layer exists. The app renders its built-in copy.
      .catch(() => setOverrides({}))
  }, [])

  // Re-check on every navigation. The provider mounts once, before sign-in,
  // when there is no token to fetch with — so without this, overrides would
  // stay empty for the whole session after logging in. `loadedFor` makes the
  // repeat check free: it only refetches when the token has actually changed
  // (signed in, signed out, switched account).
  const location = useLocation()
  const loadedFor = useRef<string | null>(null)

  useEffect(() => {
    const token = localStorage.getItem('authToken')
    if (loadedFor.current === token) return
    loadedFor.current = token
    load()
  }, [location.pathname, load])

  // The console calls reload() after saving, when the token has NOT changed —
  // so it must bypass the loadedFor guard.
  const reload = useCallback(() => {
    loadedFor.current = null
    const token = localStorage.getItem('authToken')
    loadedFor.current = token
    load()
  }, [load])

  const value = useMemo(() => ({ overrides, reload }), [overrides, reload])
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
