import { useEffect, useState } from 'react'

import { RenderedSource, questionnaireApi } from '../../api/client'

/** "para 47" / "page 3, para 12" -> 47. The loader's own block index. */
export function blockIndexOf(sourceLocation: string | null | undefined): number | null {
  const match = /para\s+(\d+)/i.exec(sourceLocation || '')
  return match ? Number(match[1]) : null
}

export interface RenderedSourceState {
  doc: RenderedSource | null
  error: string | null
  loading: boolean
}

/**
 * The client's document, rendered once and shared.
 *
 * Both the side-by-side pane and each question's own source tile want the
 * SAME document, and fetching it per component would pull ~100KB again on
 * every question click. One fetch per study, held by the page.
 */
export function useRenderedSource(studyId: string, enabled: boolean): RenderedSourceState {
  const [doc, setDoc] = useState<RenderedSource | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!enabled || doc || error || loading) return
    let cancelled = false
    setLoading(true)
    questionnaireApi
      .renderedSource(studyId)
      .then((res) => !cancelled && setDoc(res.data))
      .catch((err) => {
        const data = err?.response?.data
        const detail = typeof data === 'string' ? data : data?.detail
        !cancelled &&
          setError(
            (typeof detail === 'string' ? detail : detail?.error) ||
              'Could not open the document for reading.',
          )
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studyId, enabled])

  return { doc, error, loading }
}

/**
 * The blocks this question was read from, as the document draws them.
 *
 * A question's provenance names where it STARTS; it runs until the next
 * question starts. Taking only the first block would show the header row of a
 * spec table and none of its answers — which is the half that matters when
 * you are checking what the parser read.
 *
 * Blocks with no id are struck-out lines the loader dropped. They are kept
 * here: "the brand was struck out" is exactly what a reviewer is looking for.
 */
export function sliceForQuestion(
  html: string,
  from: number | null,
  next: number | null,
): string {
  if (from === null) return ''
  const host = document.createElement('div')
  host.innerHTML = html
  const out: Element[] = []
  let started = false
  for (const child of Array.from(host.children)) {
    const id = child.id
    const index = id.startsWith('b') ? Number(id.slice(1)) : null
    if (index !== null && Number.isFinite(index)) {
      if (index === from) started = true
      else if (started && next !== null && index >= next) break
      else if (!started) continue
    } else if (!started) {
      continue
    }
    out.push(child)
  }
  return out.map((el) => el.outerHTML).join('\n')
}
