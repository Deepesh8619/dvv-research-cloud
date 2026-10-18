import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import axios from 'axios'
import { studiesApi, type FieldedData } from '../api/client'
import FieldworkCards from './FieldworkCards'

/**
 * Fielded data that arrived as a file. A study fielded on another platform
 * (Decipher, Qualtrics…) comes back as an export, and the field monitor only
 * knew about links run here — so finished fieldwork looked like none at all.
 * This shows the export as the survey's fieldwork: who is in it, how each
 * interview ended, and the way on to checking it against the questionnaire.
 */
const OUTCOME_STYLE: Record<string, string> = {
  complete: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  terminated: 'bg-red-50 text-red-700 ring-red-600/20',
  overquota: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  partial: 'bg-slate-100 text-slate-700 ring-slate-500/20',
  other: 'bg-slate-100 text-slate-600 ring-slate-500/20',
}

export default function FieldedDataPanel({ studyId, canWrite }: { studyId: string; canWrite: boolean }) {
  const [data, setData] = useState<FieldedData | null>(null)
  const [state, setState] = useState<'idle' | 'uploading' | 'processing' | 'failed'>('idle')
  const [pct, setPct] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData((await studiesApi.fieldedData(studyId)).data)
    } catch {
      setData(null)
    }
  }, [studyId])

  useEffect(() => {
    void load()
  }, [load])

  const upload = async (file: File) => {
    setError(null)
    setState('uploading')
    setPct(0)
    try {
      await studiesApi.attachData(studyId, file, setPct)
      setState('processing')
      for (let i = 0; i < 100; i++) {
        await new Promise((r) => setTimeout(r, 3000))
        const status = (await studiesApi.get(studyId)).data.status
        if (status === 'error') {
          setState('failed')
          return
        }
        if (status !== 'classifying') {
          setState('idle')
          await load()
          return
        }
      }
      setState('failed')
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
      setError(typeof detail === 'string' ? detail : 'The data file could not be uploaded.')
      setState('idle')
    }
  }

  const busy = state === 'uploading' || state === 'processing'
  const picker = canWrite && !busy && (
    <label className="inline-flex cursor-pointer items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50">
      {data?.available ? 'Replace data file' : 'Upload fielded data (.xlsx, .sav, .csv)'}
      <input
        id="fielded-data-file"
        type="file"
        accept=".xlsx,.sav,.csv"
        className="sr-only"
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) void upload(f)
        }}
      />
    </label>
  )

  return (
    <>
    <section className="mb-6 rounded-lg border border-slate-200 bg-white p-5 shadow-panel" data-testid="fielded-data">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-slate-900">Fielded data</h2>
          <p className="mt-1 max-w-xl text-sm text-slate-500">
            {data?.available
              ? 'The respondent data in this project. Fielded on another platform? It is your fieldwork all the same.'
              : 'Fielded this survey on another platform? Upload its export here and it becomes this survey’s fieldwork.'}
          </p>
        </div>
        {picker}
      </div>

      {error && (
        <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{error}</p>
      )}
      {busy && (
        <p role="status" className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
          {state === 'uploading' ? `Uploading… ${pct}%` : 'Reading the file and its variables… usually under a minute.'}
        </p>
      )}
      {state === 'failed' && (
        <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          The file could not be read. Use the respondent-level export (one row per respondent) and try again.
        </p>
      )}

      {data?.available && !busy && (
        <div className="mt-4 space-y-3">
          <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <div className="flex gap-2">
              <dt className="text-slate-500">File</dt>
              <dd className="font-medium text-slate-800">{data.source_filename ?? 'collected through the link'}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-slate-500">Respondents</dt>
              <dd className="font-medium tabular-nums text-slate-800">{(data.n_respondents ?? 0).toLocaleString()}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-slate-500">Variables</dt>
              <dd className="font-medium tabular-nums text-slate-800">{(data.n_variables ?? 0).toLocaleString()}</dd>
            </div>
          </dl>
          {data.dispositions && data.dispositions.length > 0 ? (
            <div className="flex flex-wrap gap-2" aria-label="How each interview ended">
              {data.dispositions.map((d) => (
                <span
                  key={`${d.code}`}
                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${OUTCOME_STYLE[d.outcome] ?? OUTCOME_STYLE.other}`}
                >
                  {d.label}
                  <span className="tabular-nums">{d.n.toLocaleString()}</span>
                </span>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              The file has no status column, so every row is treated as an interview; completes and screen-outs can't be told apart.
            </p>
          )}
          <div className="flex flex-wrap gap-2 pt-1">
            <Link
              to={`/projects/${studyId}?tab=${encodeURIComponent('Questionnaire checks')}`}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-800"
            >
              Check it against the questionnaire
            </Link>
            <Link
              to={`/projects/${studyId}`}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Tables and reports
            </Link>
          </div>
        </div>
      )}
    </section>
    {data?.available && !busy && (
      <div className="mb-6">
        <FieldworkCards key={data.updated_at ?? ''} studyId={studyId} ready full />
      </div>
    )}
    </>
  )
}
