import { useEffect, useMemo, useRef, useState } from 'react'

import { RenderedSource, questionnaireApi } from '../../api/client'

/** "para 47" / "page 3, para 12" -> 47. The loader's own block index. */
export function blockIndexOf(sourceLocation: string | null | undefined): number | null {
  const match = /para\s+(\d+)/i.exec(sourceLocation || '')
  return match ? Number(match[1]) : null
}

const ZOOM_STEPS = [0.75, 0.875, 1, 1.15, 1.35, 1.6, 2]
const DEFAULT_ZOOM = 2 // index into ZOOM_STEPS -> 1.0

/**
 * The client's document, open beside our reading of it.
 *
 * The parser keeps a questionnaire's meaning and drops its presentation, and
 * the presentation is carrying information: a highlight after an option label
 * is how these documents say "a hidden variable sits behind this one", and red
 * strikethrough is this wave's edits to last wave's script. Neither survives
 * into the extracted text, so a reviewer checking our reading against the
 * source was comparing it against something that no longer showed the thing
 * they were checking for.
 *
 * Scrolls itself to the selected question, and scrolls independently once it
 * is there, because the two documents do not run at the same speed — the
 * extraction is one card per question and the source is pages of prose.
 */
export default function SourcePane({
  studyId,
  sourceLocation,
  onClose,
}: {
  studyId: string
  /** The selected question's provenance, e.g. "para 47". */
  sourceLocation: string | null | undefined
  onClose: () => void
}) {
  const [doc, setDoc] = useState<RenderedSource | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [zoom, setZoom] = useState(DEFAULT_ZOOM)
  const [follow, setFollow] = useState(true)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
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
    return () => {
      cancelled = true
    }
  }, [studyId])

  const target = useMemo(() => blockIndexOf(sourceLocation), [sourceLocation])

  // Scroll to the selected question, and mark it so the eye finds it.
  useEffect(() => {
    if (!doc || !follow || target === null || !scroller.current) return
    const root = scroller.current
    root.querySelectorAll('.dvv-here').forEach((el) => el.classList.remove('dvv-here'))
    const block = root.querySelector(`#b${target}`)
    if (!block) return
    block.classList.add('dvv-here')
    // Marking it is the part that must not fail: scrollIntoView is missing in
    // some environments, and an unguarded call takes the whole pane down.
    block.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }, [doc, follow, target, zoom])

  return (
    <aside className="flex h-full w-full flex-col overflow-hidden border-l border-slate-200 bg-white">
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-slate-900">
            {doc?.filename || 'The document'}
          </p>
          <p className="text-[11px] text-slate-500">
            As the client wrote it — colour, highlights and strikethrough kept.
          </p>
        </div>

        <label className="flex items-center gap-1.5 text-[11px] text-slate-600">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
          />
          Follow
        </label>

        <div className="flex items-center gap-1 rounded-md border border-slate-200 bg-white">
          <button
            type="button"
            aria-label="Zoom out"
            disabled={zoom === 0}
            onClick={() => setZoom((z) => Math.max(0, z - 1))}
            className="px-2 py-1 text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-30"
          >
            −
          </button>
          <button
            type="button"
            onClick={() => setZoom(DEFAULT_ZOOM)}
            title="Reset zoom"
            className="min-w-[3rem] px-1 py-1 text-[11px] tabular-nums text-slate-600 hover:bg-slate-100"
          >
            {Math.round(ZOOM_STEPS[zoom] * 100)}%
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            disabled={zoom === ZOOM_STEPS.length - 1}
            onClick={() => setZoom((z) => Math.min(ZOOM_STEPS.length - 1, z + 1))}
            className="px-2 py-1 text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-30"
          >
            +
          </button>
        </div>

        <button
          type="button"
          onClick={onClose}
          aria-label="Close the document"
          className="rounded-md px-2 py-1 text-sm text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          ✕
        </button>
      </header>

      {error && <p className="px-3 py-3 text-sm text-red-600">{error}</p>}
      {!doc && !error && (
        <p className="px-3 py-3 text-sm text-slate-400">Opening the document…</p>
      )}

      {doc && (
        <div ref={scroller} className="dvv-src min-h-0 flex-1 overflow-auto px-4 py-3">
          <div
            style={{ fontSize: `${ZOOM_STEPS[zoom] * 0.875}rem` }}
            /* Built on the server from escaped text; no markup from the
               document is ever passed through. See src/intake/document_html.py. */
            dangerouslySetInnerHTML={{ __html: doc.html }}
          />
        </div>
      )}
    </aside>
  )
}
