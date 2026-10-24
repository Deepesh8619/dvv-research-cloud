import { useCallback, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import {
  CodingPayload,
  CodingStatus,
  CodingVariable,
  ReviewItem,
  codingApi,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import LoadingSpinner from './LoadingSpinner'
import { canWrite as hasWriteRole } from '../auth/roles'

// Roles allowed to resolve items (matches api require_role(OWNER, ANALYST)).

function errorMessage(err: unknown): string | null {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    return err.message
  }
  return err instanceof Error ? err.message : null
}

/** One review-queue row: verbatim, suggestion, accept / correct actions. */
function ReviewRow({
  item,
  variable,
  studyId,
  canWrite,
  onResolved,
}: {
  item: ReviewItem
  variable: CodingVariable
  studyId: string
  canWrite: boolean
  onResolved: (respondentId: number, status: 'accepted' | 'corrected', codes: string[]) => void
}) {
  const [correcting, setCorrecting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set(item.suggested_codes))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const resolved = item.status !== 'pending'
  const finalCodes = item.final_codes ?? item.suggested_codes

  const submit = async (status: 'accepted' | 'corrected', codes?: string[]) => {
    setBusy(true)
    setError(null)
    try {
      await codingApi.resolve(studyId, variable.question_var, item.respondent_id, status, codes)
      onResolved(item.respondent_id, status, codes ?? item.suggested_codes)
      setCorrecting(false)
    } catch (err) {
      setError(errorMessage(err) || 'Could not save')
    } finally {
      setBusy(false)
    }
  }

  const toggleCode = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const labelFor = (id: string) =>
    variable.codeframe.codes.find((c) => c.id === id)?.label ?? id

  return (
    <div className={`border-b border-slate-100 px-4 py-3 ${resolved ? 'bg-emerald-50/40' : ''}`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm text-slate-800">“{item.verbatim}”</p>
          <p className="text-xs text-slate-400 mt-1">
            respondent {item.respondent_id} · confidence{' '}
            <span className={item.confidence < 0.5 ? 'text-red-500 font-medium' : ''}>
              {(item.confidence * 100).toFixed(0)}%
            </span>
            {item.reasoning && <> · {item.reasoning}</>}
          </p>
          <div className="flex flex-wrap gap-1 mt-2">
            {finalCodes.length === 0 && (
              <span className="text-xs text-slate-400 italic">no codes suggested</span>
            )}
            {finalCodes.map((c) => (
              <span
                key={c}
                className="text-[11px] bg-sky-50 text-sky-700 border border-sky-200 rounded px-1.5 py-0.5"
              >
                {labelFor(c)}
              </span>
            ))}
          </div>
        </div>
        <div className="shrink-0 text-right">
          {resolved ? (
            <span className="text-xs text-emerald-600 font-medium">
              ✓ {item.status}
              {item.resolved_by ? ` · ${item.resolved_by}` : ''}
            </span>
          ) : canWrite ? (
            <div className="flex gap-2">
              <button
                onClick={() => submit('accepted')}
                disabled={busy}
                className="text-xs bg-emerald-600 text-white font-medium px-2.5 py-1 rounded hover:bg-emerald-700 disabled:opacity-50"
              >
                Accept
              </button>
              <button
                onClick={() => setCorrecting((v) => !v)}
                disabled={busy}
                className="text-xs border border-slate-300 text-slate-600 font-medium px-2.5 py-1 rounded hover:bg-slate-50 disabled:opacity-50"
              >
                {correcting ? 'Cancel' : 'Correct'}
              </button>
            </div>
          ) : (
            <span className="text-xs text-slate-400">read-only</span>
          )}
        </div>
      </div>

      {correcting && !resolved && (
        <div className="mt-3 bg-slate-50 rounded-md p-3">
          <p className="text-xs font-medium text-slate-600 mb-2">Assign codes:</p>
          <div className="flex flex-wrap gap-2">
            {variable.codeframe.codes.map((c) => (
              <label
                key={c.id}
                className={`text-xs border rounded px-2 py-1 cursor-pointer select-none ${
                  selected.has(c.id)
                    ? 'bg-sky-100 border-sky-300 text-sky-800'
                    : 'bg-white border-slate-200 text-slate-600'
                }`}
                title={c.description}
              >
                <input
                  type="checkbox"
                  className="hidden"
                  checked={selected.has(c.id)}
                  onChange={() => toggleCode(c.id)}
                />
                {c.label}
              </label>
            ))}
          </div>
          <button
            onClick={() => submit('corrected', [...selected])}
            disabled={busy}
            className="mt-3 text-xs bg-primary-500 text-white font-medium px-3 py-1.5 rounded hover:bg-primary-600 disabled:opacity-50"
          >
            {busy ? 'Saving…' : 'Save correction'}
          </button>
        </div>
      )}
      {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
    </div>
  )
}

function VariableQueue({
  variable,
  studyId,
  canWrite,
  onResolved,
}: {
  variable: CodingVariable
  studyId: string
  canWrite: boolean
  onResolved: (
    variable: string,
    respondentId: number,
    status: 'accepted' | 'corrected',
    codes: string[],
  ) => void
}) {
  const pending = variable.review_queue.filter((i) => i.status === 'pending').length
  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-panel overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-200 flex items-center justify-between">
        <div>
          <span className="text-sm font-semibold text-slate-800">{variable.question_var}</span>
          <span className="text-xs text-slate-400 ml-2">
            {variable.summary.n_auto_coded} auto-coded · {variable.review_queue.length} queued ·{' '}
            {pending} pending
          </span>
        </div>
        <span className="text-xs text-slate-400">
          {variable.codeframe.codes.length} codes · mean confidence{' '}
          {(variable.summary.confidence_mean * 100).toFixed(0)}%
        </span>
      </div>
      {variable.note && (
        <div className="px-4 py-3 bg-amber-50 border-b border-amber-100 text-sm text-amber-800">
          {variable.note}
        </div>
      )}
      <div className="max-h-[60vh] overflow-y-auto">
        {variable.review_queue.map((item) => (
          <ReviewRow
            key={item.respondent_id}
            item={item}
            variable={variable}
            studyId={studyId}
            canWrite={canWrite}
            onResolved={(rid, status, codes) =>
              onResolved(variable.question_var, rid, status, codes)
            }
          />
        ))}
        {variable.review_queue.length === 0 && !variable.note && (
          <p className="text-sm text-slate-400 p-4">
            Nothing queued — every verbatim was coded above the confidence threshold.
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * A project's open questions: which are coded, a button to code them (codeframe
 * from a sample, every answer coded by an open model, in the background), and
 * the low-confidence answers a person should check.
 */
export default function OpenEndsPanel({ studyId }: { studyId: string }) {
  const { user } = useAuth()
  const canWrite = hasWriteRole(user?.role)
  const [status, setStatus] = useState<CodingStatus | null>(null)
  const [coding, setCoding] = useState<CodingPayload | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [st, cd] = await Promise.all([codingApi.status(studyId), codingApi.get(studyId).catch(() => null)])
      setStatus(st.data)
      setCoding(cd?.data ?? null)
      setError(null)
    } catch (err) {
      setError(errorMessage(err) || 'Could not read this study’s open questions.')
    }
  }, [studyId])
  useEffect(() => { void load() }, [load])

  const running = status?.job?.state === 'running'
  // Coding runs in the background: check back every few seconds until it ends.
  useEffect(() => {
    if (!running) return
    const t = window.setInterval(() => void load(), 4000)
    return () => window.clearInterval(t)
  }, [running, load])

  const onResolved = useCallback(
    (variable: string, respondentId: number, st: 'accepted' | 'corrected', codes: string[]) => {
      setCoding((prev) => {
        const block = prev?.[variable]
        if (!prev || !block) return prev
        return { ...prev, [variable]: { ...block, review_queue: block.review_queue.map((i) =>
          i.respondent_id === respondentId ? { ...i, status: st, final_codes: codes, resolved_by: user?.name } : i) } }
      })
    },
    [user?.name],
  )
  const coded = useMemo(() => (coding ? Object.values(coding).filter((v) => v.codeframe.codes.length > 0) : []), [coding])

  if (error) return <p className="text-sm text-red-700">{error}</p>
  if (!status) return <LoadingSpinner />
  const uncoded = status.open_ends.filter((o) => !o.coded)

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-panel">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-semibold text-slate-900">Open questions</h2>
            <p className="mt-1 max-w-2xl text-sm text-slate-500">
              Coding builds a codeframe from a sample of answers, codes every answer, and sends the ones it is unsure
              of to the list below for a person to check.
            </p>
          </div>
          {canWrite && status.open_ends.length > 0 && (
            <button type="button" disabled={running} onClick={async () => {
              try { await codingApi.run(studyId, uncoded.length ? uncoded.map((o) => o.variable) : undefined); await load() }
              catch (err) { setError(errorMessage(err)) }
            }} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
              {running ? 'Coding…' : uncoded.length ? `Code ${uncoded.length} open question${uncoded.length === 1 ? '' : 's'}` : 'Code again'}
            </button>
          )}
        </div>
        {status.open_ends.length === 0 ? (
          <p className="mt-3 text-sm text-slate-600">This study has no open questions.</p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100 text-sm">
            {status.open_ends.map((o) => (
              <li key={o.variable} className="flex items-baseline gap-3 py-2">
                <span className={`h-2 w-2 shrink-0 rounded-full ${o.coded ? 'bg-emerald-500' : 'bg-slate-300'}`} aria-hidden />
                <span className="font-mono text-slate-900">{o.variable}</span>
                <span className="min-w-0 flex-1 truncate text-slate-600">{o.question}</span>
                <span className="text-xs text-slate-500">{o.n_answers} answers · {o.coded ? 'coded' : 'not coded'}</span>
              </li>
            ))}
          </ul>
        )}
        {status.job?.state === 'failed' && <p className="mt-3 text-sm text-red-700">{status.job.error}</p>}
        {status.job?.state === 'done' && Object.keys(status.job.failed ?? {}).length > 0 && (
          <p className="mt-3 text-sm text-amber-800">
            Not coded: {Object.entries(status.job.failed ?? {}).map(([k, v]) => `${k} (${v})`).join('; ')}
          </p>
        )}
      </section>
      {coded.map((v) => (
        <VariableQueue key={v.question_var} variable={v} studyId={studyId} canWrite={canWrite} onResolved={onResolved} />
      ))}
    </div>
  )
}
