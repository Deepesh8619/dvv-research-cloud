import { useState } from 'react'

import { MaskLink, Question, QuestionMapEntry, questionnaireApi } from '../../api/client'
import { BUCKETS, bucketInfo } from '../../lib/buckets'

// The review screen reads the questionnaire the way a researcher does: by kind
// (screener, profile, core, loops, open ends, hidden) and by dependency (what
// decides who sees this, whose answers fill its options, what it decides in
// turn). Every link comes from what the document states; a reviewer can add
// or take one away, and the server refuses a link that points forwards.

const MODES: Array<{ key: string; label: string; hint: string }> = [
  { key: 'selected', label: 'Only what they picked', hint: 'Show just the answers chosen there' },
  { key: 'full_list', label: 'The whole list', hint: 'Same list, whatever was chosen' },
  { key: 'not_selected', label: 'What they did not pick', hint: 'Everything except the answers chosen there' },
]

function masksOf(q: Question): MaskLink[] {
  const t = (q.validation?.techniques ?? {}) as { masks?: MaskLink[] }
  return (t.masks ?? []).filter((m) => m.source_qid)
}

function pipesOf(q: Question): string[] {
  const t = (q.validation?.techniques ?? {}) as { pipes?: Array<{ source_qid?: string | null }> }
  return (t.pipes ?? []).map((p) => p.source_qid).filter((x): x is string => !!x)
}

// ---------------------------------------------------------------- bucket bar

export function BucketBar({
  counts,
  total,
  value,
  onChange,
}: {
  counts: Record<string, number>
  total: number
  value: string
  onChange: (key: string) => void
}) {
  const items = [{ key: 'all', label: 'All', hint: 'Every question', dot: 'bg-slate-300' }, ...BUCKETS]
  return (
    <div role="tablist" aria-label="Buckets" className="flex gap-1.5 overflow-x-auto px-3 py-2.5 [scrollbar-width:none]">
      {items.map((b) => {
        const n = b.key === 'all' ? total : counts[b.key] ?? 0
        if (!n) return null
        const on = value === b.key
        return (
          <button
            key={b.key}
            type="button"
            role="tab"
            aria-selected={on}
            title={b.hint}
            onClick={() => onChange(b.key)}
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-all duration-200 active:scale-95 ${
              on
                ? 'border-slate-900 bg-slate-900 text-white shadow-sm'
                : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900'
            }`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${b.dot}`} />
            {b.label}
            <span className={`tabular-nums ${on ? 'text-slate-300' : 'text-slate-400'}`}>{n}</span>
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------- progress

/** Approved out of total, as a ring that fills. */
export function ProgressRing({ done, total }: { done: number; total: number }) {
  const r = 18
  const c = 2 * Math.PI * r
  const share = total > 0 ? done / total : 0
  return (
    <div className="relative h-12 w-12 shrink-0" title={`${done} of ${total} approved`}>
      <svg viewBox="0 0 44 44" className="h-12 w-12 -rotate-90">
        <circle cx="22" cy="22" r={r} fill="none" stroke="#e2e8f0" strokeWidth="4" />
        <circle
          cx="22" cy="22" r={r} fill="none" strokeWidth="4" strokeLinecap="round"
          stroke={share === 1 ? '#10b981' : '#6366f1'}
          strokeDasharray={c}
          strokeDashoffset={c * (1 - share)}
          className="motion-safe:transition-[stroke-dashoffset] motion-safe:duration-700 motion-safe:ease-out"
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-[11px] font-semibold tabular-nums text-slate-700">
        {Math.round(share * 100)}%
      </span>
    </div>
  )
}

// ---------------------------------------------------------------- issues

const _QID_IN_ISSUE = /question\s+([A-Za-z][\w.]*):/

/** Validation issues as a list you can act on: each one opens its question. */
export function IssuesPanel({ errors, onGoTo }: { errors: string[]; onGoTo: (qid: string) => void }) {
  const [open, setOpen] = useState(true)
  if (errors.length === 0) return null
  return (
    <div className="mb-4 overflow-hidden rounded-xl border border-amber-200 bg-amber-50/70 dvv-rise">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2 text-sm font-semibold text-amber-900">
          <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-amber-500 px-1.5 text-[11px] text-white tabular-nums">
            {errors.length}
          </span>
          {errors.length === 1 ? 'Issue needs attention' : 'Issues need attention'}
        </span>
        <span className={`text-amber-700 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}>▾</span>
      </button>
      {open && (
        <ul className="grid gap-1.5 px-4 pb-3 sm:grid-cols-2">
          {errors.map((e, i) => {
            const qid = _QID_IN_ISSUE.exec(e)?.[1]
            const text = qid ? e.replace(_QID_IN_ISSUE, '').trim() : e
            return (
              <li key={i}>
                <button
                  type="button"
                  disabled={!qid}
                  onClick={() => qid && onGoTo(qid)}
                  className="flex w-full items-start gap-2 rounded-lg bg-white/70 px-3 py-2 text-left text-sm text-amber-900 ring-1 ring-amber-200/70 transition-all hover:bg-white hover:shadow-sm disabled:cursor-default"
                >
                  {qid && <span className="shrink-0 font-mono text-xs font-semibold text-amber-700">{qid}</span>}
                  <span className="leading-snug">{text.replace(/_/g, ' ')}</span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- connections

function LinkChip({
  qid,
  onGo,
  onRemove,
  tone,
  note,
}: {
  qid: string
  onGo: (qid: string) => void
  onRemove?: () => void
  tone: string
  note?: string
}) {
  return (
    <span className={`group inline-flex items-center overflow-hidden rounded-lg ring-1 ring-inset dvv-pop ${tone}`}>
      <button
        type="button"
        onClick={() => onGo(qid)}
        className="px-2 py-1 font-mono text-xs font-semibold hover:underline"
        title={`Go to ${qid}`}
      >
        {qid}
        {note && <span className="ml-1 font-sans font-normal opacity-70">· {note}</span>}
      </button>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove link to ${qid}`}
          className="border-l border-current/10 px-1.5 py-1 text-xs opacity-50 transition-opacity hover:opacity-100"
        >
          ×
        </button>
      )}
    </span>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <span className="w-28 shrink-0 pt-1 text-xs font-medium text-slate-500">{label}</span>
      <div className="flex flex-1 flex-wrap gap-1.5">{children}</div>
    </div>
  )
}

/**
 * How this question connects: what it depends on, and what its answer decides.
 * "Options from" and "Text uses" can be added and removed here; "Asked if" is
 * the routing editor below, and "Decides" is the other questions' own links.
 */
export function ConnectionsPanel({
  studyId,
  question,
  entry,
  earlier,
  canWrite,
  onChanged,
  onGoTo,
}: {
  studyId: string
  question: Question
  entry: QuestionMapEntry | undefined
  /** Questions before this one — the only ones it may take from. */
  earlier: Question[]
  canWrite: boolean
  onChanged: (updated: Question) => void
  onGoTo: (qid: string) => void
}) {
  const [adding, setAdding] = useState<null | 'options' | 'text'>(null)
  const [source, setSource] = useState('')
  const [mode, setMode] = useState('selected')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const links = entry?.links
  const masks = masksOf(question)
  const pipes = pipesOf(question)
  const bucket = bucketInfo(entry?.bucket)

  const save = async (body: { options_from?: MaskLink[]; text_from?: string[] }) => {
    setSaving(true)
    setError(null)
    try {
      const res = await questionnaireApi.setLinks(studyId, question.qid, body)
      onChanged(res.data)
      setAdding(null)
      setSource('')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'Could not save the link.')
    } finally {
      setSaving(false)
    }
  }

  const add = () => {
    if (!source) return
    if (adding === 'options') {
      void save({ options_from: [...masks.filter((m) => m.source_qid !== source), { source_qid: source, mode }] })
    } else {
      void save({ text_from: [...pipes.filter((p) => p !== source), source] })
    }
  }

  const modeLabel = (m: string) => MODES.find((x) => x.key === m)?.label.toLowerCase()

  return (
    <section className="mb-6 rounded-xl border border-slate-200 bg-gradient-to-b from-slate-50/80 to-white p-4 dvv-rise">
      <header className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-900">Connections</h3>
        {bucket && (
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${bucket.chip}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${bucket.dot}`} />
            {bucket.label}
          </span>
        )}
        {entry?.suggested && (
          <span className="text-[11px] text-amber-700" title={entry.reason}>
            placed by wording — check
          </span>
        )}
      </header>

      <div className="space-y-2">
        <Row label="Asked if">
          {links?.asked_if.length ? (
            links.asked_if.map((q) => (
              <LinkChip key={q} qid={q} onGo={onGoTo} tone="bg-sky-50 text-sky-800 ring-sky-200" />
            ))
          ) : (
            <span className="pt-1 text-xs text-slate-400">Everyone — set a condition in routing below</span>
          )}
        </Row>
        <Row label="Options from">
          {masks.map((m) => (
            <LinkChip
              key={m.source_qid}
              qid={m.source_qid}
              note={modeLabel(m.mode)}
              onGo={onGoTo}
              tone="bg-violet-50 text-violet-800 ring-violet-200"
              onRemove={canWrite ? () => void save({ options_from: masks.filter((x) => x !== m) }) : undefined}
            />
          ))}
          {!masks.length && <span className="pt-1 text-xs text-slate-400">Its own list</span>}
        </Row>
        <Row label="Text uses">
          {pipes.map((p) => (
            <LinkChip
              key={p}
              qid={p}
              onGo={onGoTo}
              tone="bg-fuchsia-50 text-fuchsia-800 ring-fuchsia-200"
              onRemove={canWrite ? () => void save({ text_from: pipes.filter((x) => x !== p) }) : undefined}
            />
          ))}
          {!pipes.length && <span className="pt-1 text-xs text-slate-400">No earlier answer in the wording</span>}
        </Row>
        {!!links?.computed_from.length && (
          <Row label="Computed from">
            {links.computed_from.map((q) => (
              <LinkChip key={q} qid={q} onGo={onGoTo} tone="bg-slate-100 text-slate-700 ring-slate-200" />
            ))}
          </Row>
        )}
        {links?.loop_of && (
          <Row label="Loop of">
            <LinkChip qid={links.loop_of} onGo={onGoTo} tone="bg-emerald-50 text-emerald-800 ring-emerald-200" />
          </Row>
        )}
        <Row label="Decides">
          {links?.used_by.length ? (
            links.used_by.map((q) => (
              <LinkChip key={q} qid={q} onGo={onGoTo} tone="bg-indigo-50 text-indigo-800 ring-indigo-200" />
            ))
          ) : (
            <span className="pt-1 text-xs text-slate-400">No later question depends on this answer</span>
          )}
        </Row>
      </div>

      {canWrite && !adding && (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => { setAdding('options'); setMode('selected') }}
            className="rounded-lg border border-violet-200 bg-white px-3 py-1.5 text-xs font-medium text-violet-700 transition-all hover:border-violet-300 hover:bg-violet-50 active:scale-95"
          >
            + Options from…
          </button>
          <button
            type="button"
            onClick={() => setAdding('text')}
            className="rounded-lg border border-fuchsia-200 bg-white px-3 py-1.5 text-xs font-medium text-fuchsia-700 transition-all hover:border-fuchsia-300 hover:bg-fuchsia-50 active:scale-95"
          >
            + Pipe an answer into the text…
          </button>
        </div>
      )}

      {adding && (
        <div className="mt-4 rounded-lg border border-slate-200 bg-white p-3 shadow-sm dvv-pop">
          <p className="mb-2 text-xs font-medium text-slate-700">
            {adding === 'options'
              ? 'Show this question’s options from an earlier question'
              : 'Write an earlier answer into this question’s wording'}
          </p>
          <select
            aria-label="Source question"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
          >
            <option value="">Choose an earlier question…</option>
            {earlier.map((q) => (
              <option key={q.qid} value={q.qid}>
                {q.qid} — {q.text.slice(0, 70)}
              </option>
            ))}
          </select>
          {adding === 'options' && (
            <div className="mt-2 grid gap-1.5 sm:grid-cols-3" role="radiogroup" aria-label="Which options">
              {MODES.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  role="radio"
                  aria-checked={mode === m.key}
                  title={m.hint}
                  onClick={() => setMode(m.key)}
                  className={`rounded-md border px-2 py-1.5 text-xs transition-colors ${
                    mode === m.key
                      ? 'border-violet-500 bg-violet-50 font-medium text-violet-800'
                      : 'border-slate-200 text-slate-600 hover:border-slate-300'
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          )}
          {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => { setAdding(null); setError(null) }}
              className="rounded-md px-3 py-1.5 text-xs text-slate-500 hover:text-slate-800"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!source || saving}
              onClick={add}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white transition-all hover:bg-slate-700 active:scale-95 disabled:opacity-40"
            >
              {saving ? 'Saving…' : 'Add link'}
            </button>
          </div>
        </div>
      )}
      {error && !adding && <p className="mt-2 text-xs text-red-600">{error}</p>}
    </section>
  )
}
