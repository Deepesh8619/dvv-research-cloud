import { useMemo, useState } from 'react'

import { RenderedSource } from '../../api/client'
import { blockIndexOf, sliceForQuestion } from './useRenderedSource'

/**
 * What this question was read from — drawn as the document draws it.
 *
 * The parser flattens a spec table into "Label: value" lines so it can read
 * them, and the tile was showing that flattening. A reviewer checking our
 * reading against the source was then comparing two different shapes: a table
 * in Word, a list of lines here. Every row they had to re-associate by eye was
 * a chance to miss the one that is wrong.
 *
 * So the default is the document's own table, with its colour and highlights.
 * The parser's flattened reading stays one click away, because when the two
 * disagree THAT is the thing worth looking at.
 */
export default function SourceTile({
  sourceText,
  sourceLocation,
  doc,
  nextLocation,
  onOpenDocument,
}: {
  sourceText: string
  sourceLocation: string | null
  doc: RenderedSource | null
  /** Where the NEXT question starts, so this one knows where it ends. */
  nextLocation: string | null
  onOpenDocument?: () => void
}) {
  const [asParsed, setAsParsed] = useState(false)
  const [open, setOpen] = useState(true)

  const slice = useMemo(() => {
    if (!doc) return ''
    return sliceForQuestion(doc.html, blockIndexOf(sourceLocation), blockIndexOf(nextLocation))
  }, [doc, sourceLocation, nextLocation])

  const canShowDocument = !!slice && !asParsed

  return (
    <div className="rounded-md border border-slate-200 bg-slate-50">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-3 py-1.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="text-xs font-medium uppercase tracking-wide text-slate-500 hover:text-slate-800"
        >
          {open ? '▾' : '▸'} Source
        </button>
        {sourceLocation && <span className="text-xs text-slate-400">{sourceLocation}</span>}

        <div className="ml-auto flex items-center gap-1">
          {slice && (
            <div className="flex overflow-hidden rounded border border-slate-300 bg-white text-[11px]">
              <button
                type="button"
                onClick={() => setAsParsed(false)}
                className={`px-2 py-0.5 ${!asParsed ? 'bg-slate-800 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
              >
                As in document
              </button>
              <button
                type="button"
                onClick={() => setAsParsed(true)}
                title="The flattened text the parser actually read — check this when the two disagree"
                className={`px-2 py-0.5 ${asParsed ? 'bg-slate-800 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
              >
                As parsed
              </button>
            </div>
          )}
          {onOpenDocument && (
            <button
              type="button"
              onClick={onOpenDocument}
              title="Open the whole document beside this"
              className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-100"
            >
              Open ↗
            </button>
          )}
        </div>
      </div>

      {open && (
        <div className="max-h-80 overflow-auto p-3">
          {canShowDocument ? (
            <div
              className="dvv-src text-sm"
              /* Built server-side from escaped text — see document_html.py. */
              dangerouslySetInnerHTML={{ __html: slice }}
            />
          ) : (
            <p className="whitespace-pre-wrap text-sm text-slate-600">{sourceText}</p>
          )}
        </div>
      )}
    </div>
  )
}
