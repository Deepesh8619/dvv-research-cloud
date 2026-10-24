import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { HELP, type HelpId } from '../../help/help'
import { InfoIcon } from '../Icons'

/**
 * A small (i) beside a feature: what it is, why it matters, how to use it.
 * Opens on hover, on keyboard focus and on tap (phones have no hover); closes
 * on Escape, on a click elsewhere, on scroll, or when the pointer leaves.
 *
 * Drawn at the top of the page (a portal, fixed position) rather than inside
 * the feature, so a scrolling list or a clipped card cannot cut it off, and
 * kept inside the window at any width. The text lives in help/help.ts so every
 * explanation reads the same way.
 */
const WIDTH = 320

export default function InfoTip({ id, align = 'left' }: { id: HelpId; align?: 'left' | 'right' }) {
  const help = HELP[id]
  const [at, setAt] = useState<{ top: number; left: number } | null>(null)
  const tipId = useId()
  const button = useRef<HTMLButtonElement>(null)

  const show = () => {
    const r = button.current?.getBoundingClientRect()
    if (!r) return
    const width = Math.min(WIDTH, window.innerWidth - 16)
    const wanted = align === 'right' ? r.right - width : r.left - 8
    setAt({ top: r.bottom + 6, left: Math.max(8, Math.min(wanted, window.innerWidth - width - 8)) })
  }
  const hide = () => setAt(null)

  useEffect(() => {
    if (!at) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') hide() }
    const onDown = (e: PointerEvent) => { if (!button.current?.contains(e.target as Node)) hide() }
    window.addEventListener('keydown', onKey)
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('scroll', hide, true)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('scroll', hide, true)
    }
  }, [at])

  return (
    <>
      <button ref={button} type="button" aria-label={`About ${help.title}`} aria-expanded={!!at}
        aria-describedby={at ? tipId : undefined}
        onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); show() }}
        className="ml-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full align-middle text-slate-400 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500">
        <InfoIcon className="h-4 w-4" />
      </button>
      {at && createPortal(
        <div id={tipId} role="tooltip" style={{ top: at.top, left: at.left, width: Math.min(WIDTH, window.innerWidth - 16) }}
          className="pointer-events-none fixed z-50 rounded-lg border border-slate-200 bg-white p-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-slate-700 shadow-lg">
          <p className="text-sm font-semibold text-slate-900">{help.title}</p>
          <p className="mt-1.5"><span className="font-medium text-slate-900">What it is. </span>{help.what}</p>
          <p className="mt-1.5"><span className="font-medium text-slate-900">Why. </span>{help.why}</p>
          <p className="mt-1.5"><span className="font-medium text-slate-900">How. </span>{help.how}</p>
        </div>,
        document.body,
      )}
    </>
  )
}
