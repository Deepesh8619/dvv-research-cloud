import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import axios from 'axios'
import {
  CodingPayload,
  CodingVariable,
  ReviewItem,
  Study,
  codingApi,
  studiesApi,
} from '../api/client'
import { useAuth } from '../auth/AuthContext'
import LoadingSpinner from '../components/LoadingSpinner'

// Roles allowed to resolve items (matches api require_role(OWNER, ANALYST)).
const WRITE_ROLES = new Set(['owner', 'analyst'])

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
    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
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
        {variable.review_queue.length === 0 && (
          <p className="text-sm text-slate-400 p-4">
            Nothing queued — every verbatim was coded above the confidence threshold.
          </p>
        )}
      </div>
    </div>
  )
}

export default function CodingPage() {
  const { user } = useAuth()
  const canWrite = !!user && WRITE_ROLES.has(user.role)

  const [studies, setStudies] = useState<Study[]>([])
  const [selectedStudy, setSelectedStudy] = useState<string>('')
  const [coding, setCoding] = useState<CodingPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingCoding, setLoadingCoding] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    studiesApi
      .list()
      .then((res) => setStudies(res.data.studies ?? []))
      .catch(() => setStudies([]))
      .finally(() => setLoading(false))
  }, [])

  const loadCoding = useCallback(async (studyId: string) => {
    setSelectedStudy(studyId)
    setCoding(null)
    setNotice(null)
    if (!studyId) return
    setLoadingCoding(true)
    try {
      const res = await codingApi.get(studyId)
      setCoding(res.data)
    } catch (err) {
      // 404 = no open-ends / pre-persistence run: an expected state, not a failure.
      setNotice(errorMessage(err) || 'No coding data for this study')
    } finally {
      setLoadingCoding(false)
    }
  }, [])

  const onResolved = useCallback(
    (
      variable: string,
      respondentId: number,
      status: 'accepted' | 'corrected',
      codes: string[],
    ) => {
      setCoding((prev) => {
        if (!prev) return prev
        const block = prev[variable]
        if (!block) return prev
        return {
          ...prev,
          [variable]: {
            ...block,
            review_queue: block.review_queue.map((i) =>
              i.respondent_id === respondentId
                ? { ...i, status, final_codes: codes, resolved_by: user?.name }
                : i,
            ),
          },
        }
      })
    },
    [user?.name],
  )

  const variables = useMemo(() => (coding ? Object.values(coding) : []), [coding])

  if (loading) return <LoadingSpinner />

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-semibold text-slate-900 mb-1">Verbatim Coding</h1>
      <p className="text-sm text-slate-500 mb-6">
        Review low-confidence AI codings — accept the suggestion or correct the codes.
      </p>

      <div className="bg-white rounded-lg border border-slate-200 p-4 mb-6 flex items-center gap-3">
        <label className="text-sm text-slate-600 shrink-0">Project:</label>
        <select
          value={selectedStudy}
          onChange={(e) => loadCoding(e.target.value)}
          className="flex-1 h-9 px-3 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
        >
          <option value="">Select a project…</option>
          {studies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.client_name} ({s.id})
            </option>
          ))}
        </select>
        {selectedStudy && (
          <Link
            to={`/projects/${selectedStudy}`}
            className="text-sm text-sky-600 hover:text-sky-700 shrink-0"
          >
            Open project →
          </Link>
        )}
      </div>

      {loadingCoding && <LoadingSpinner />}

      {notice && !loadingCoding && (
        <div className="bg-slate-50 border border-slate-200 rounded-md p-4 text-sm text-slate-500">
          {notice}
        </div>
      )}

      {!selectedStudy && !notice && (
        <div className="bg-white rounded-lg border border-slate-200 p-10 text-center">
          <p className="text-sm text-slate-500 max-w-md mx-auto">
            When a project's data contains open-ended questions, the pipeline generates a
            codeframe, codes every response with a confidence score, and queues low-confidence
            items here. Pick a project above to review its queue.
          </p>
        </div>
      )}

      <div className="space-y-6">
        {variables.map((v) => (
          <VariableQueue
            key={v.question_var}
            variable={v}
            studyId={selectedStudy}
            canWrite={canWrite}
            onResolved={onResolved}
          />
        ))}
      </div>
    </div>
  )
}
