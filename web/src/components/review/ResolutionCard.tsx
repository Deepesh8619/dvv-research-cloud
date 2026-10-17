import { useState, type ReactNode } from 'react'
import type { Question, Resolution, SecondLookRecord } from '../../api/client'

/**
 * One answer per flagged question, instead of a row of flag codes.
 *
 * The second look (src/engines/questionnaire/second_look.py) re-checks every
 * flagged question with the whole questionnaire in view and ends in one of four
 * states. Each is said in one plain sentence with the next step on a button:
 * an automatic fix can be undone, a ready fix applied, and what only a person
 * can decide comes with the recommended action. The technical flags are still
 * there, folded away, for whoever wants them.
 */

const STATUS: Record<Resolution['status'], { label: string; chip: string; card: string }> = {
  fixed: {
    label: 'Fixed automatically',
    chip: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
    card: 'border-emerald-200 bg-emerald-50/50',
  },
  ready: {
    label: 'Fix ready',
    chip: 'bg-sky-50 text-sky-700 ring-sky-600/20',
    card: 'border-sky-200 bg-sky-50/50',
  },
  explained: {
    label: 'Note',
    chip: 'bg-slate-100 text-slate-600 ring-slate-500/20',
    card: 'border-slate-200 bg-slate-50',
  },
  needs_you: {
    label: 'Needs you',
    chip: 'bg-amber-50 text-amber-700 ring-amber-600/20',
    card: 'border-amber-200 bg-amber-50/50',
  },
}

export function resolutionOf(q: Question): Resolution | null {
  const r = q.validation?.resolution as Resolution | undefined
  return r && r.status in STATUS ? r : null
}

export function ResolutionBadge({ resolution }: { resolution: Resolution }) {
  const s = STATUS[resolution.status]
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ring-1 ring-inset ${s.chip}`}
      title={resolution.summary}
    >
      {s.label}
    </span>
  )
}

function bySource(source: string): string {
  return source.startsWith('ai:') ? `worked out by AI (${source.slice(3)})` : 'worked out from the document'
}

export default function ResolutionCard({
  resolution,
  canWrite,
  onUse,
  details,
}: {
  resolution: Resolution
  canWrite: boolean
  /** Put a question in the editor, where it is reviewed and saved. */
  onUse: (q: Question) => void
  /** The technical flags, shown on request. */
  details?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [used, setUsed] = useState(false)
  const s = STATUS[resolution.status]
  const action =
    resolution.status === 'ready' && resolution.proposed
      ? { label: 'Apply this fix', q: resolution.proposed }
      : resolution.status === 'fixed' && resolution.before
        ? { label: 'Undo', q: resolution.before }
        : null

  return (
    <div className={`rounded-md border p-3 space-y-2 ${s.card}`} data-testid="resolution">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <ResolutionBadge resolution={resolution} />
          <p className="text-sm font-medium text-slate-900">{resolution.summary}</p>
          {resolution.detail && <p className="text-xs text-slate-600">{resolution.detail}</p>}
          {resolution.status !== 'needs_you' && (
            <p className="text-[11px] text-slate-400">{bySource(resolution.source)}</p>
          )}
        </div>
        {canWrite && action && (
          <button
            type="button"
            className="shrink-0 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            disabled={used}
            onClick={() => {
              onUse(action.q)
              setUsed(true)
            }}
          >
            {used ? 'In the editor — save to keep' : action.label}
          </button>
        )}
      </div>
      {details && (
        <div>
          <button
            type="button"
            className="text-[11px] text-slate-500 underline-offset-2 hover:underline"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? 'Hide technical details' : 'Technical details'}
          </button>
          {open && <div className="mt-2">{details}</div>}
        </div>
      )}
    </div>
  )
}

/** One line over the question list: what the second look did across the document. */
export function SecondLookSummary({ record }: { record?: SecondLookRecord | null }) {
  if (!record || record.fixed + record.ready + record.explained + record.needs_you === 0) return null
  const parts: [number, string, string][] = [
    [record.fixed, 'fixed automatically', 'text-emerald-700'],
    [record.ready, 'fixes ready to apply', 'text-sky-700'],
    [record.explained, 'notes', 'text-slate-600'],
    [record.needs_you, 'need you', 'text-amber-700'],
  ]
  return (
    <p className="text-xs text-slate-600" data-testid="second-look-summary">
      <span className="font-medium text-slate-800">Second look: </span>
      {parts
        .filter(([n]) => n > 0)
        .map(([n, what, color], i) => (
          <span key={what}>
            {i > 0 && ' · '}
            <span className={`font-medium ${color}`}>{n}</span> {what}
          </span>
        ))}
    </p>
  )
}
