import { useCallback, useEffect, useState } from 'react'
import axios from 'axios'

import { studiesApi, type FieldedData } from '../api/client'
import InfoTip from './ui/InfoTip'

/**
 * The project's one data file. Uploaded here once, it is what the data check,
 * the fieldwork cards, cleaning, tables and reports all read — there is no
 * other place to upload it, so no two screens can disagree about which file
 * the project holds.
 */
export const DATA_CHANGED = 'dvv:data-changed'

export default function ProjectDataBar({ studyId, canWrite }: { studyId: string; canWrite: boolean }) {
  const [data, setData] = useState<FieldedData | null>(null)
  const [state, setState] = useState<'idle' | 'uploading' | 'processing' | 'failed'>('idle')
  const [pct, setPct] = useState(0)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try { setData((await studiesApi.fieldedData(studyId)).data) } catch { setData(null) }
  }, [studyId])
  useEffect(() => { void load() }, [load])

  const upload = async (file: File) => {
    setError(null); setState('uploading'); setPct(0)
    try {
      await studiesApi.attachData(studyId, file, setPct)
      setState('processing')
      for (let i = 0; i < 100; i++) {
        await new Promise((r) => setTimeout(r, 3000))
        const status = (await studiesApi.get(studyId)).data.status
        if (status === 'error') { setState('failed'); return }
        if (status !== 'classifying') {
          setState('idle')
          await load()
          window.dispatchEvent(new Event(DATA_CHANGED))  // every panel reading the data reloads
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
  const has = !!data?.available && !!data.source_filename
  return (
    <section className="mb-4 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-panel" data-testid="project-data">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <span className="inline-flex items-center text-slate-500">Data<InfoTip id="data" /></span>
        {has ? (
          <>
            <span className="font-medium text-slate-900">{data!.source_filename}</span>
            <span className="text-slate-500">
              {data!.n_respondents} respondents · {data!.n_variables} variables{data!.n_coded_open_ends ? ` · ${data!.n_coded_open_ends} open ends coded` : ''}
            </span>
            <button type="button" className="text-primary-600 hover:underline"
              onClick={() => studiesApi.download(studyId, 'source', data!.source_filename!)}>Download</button>
          </>
        ) : (
          <span className="text-slate-600">No data yet. Upload the respondent-level export (one row per respondent).</span>
        )}
        {canWrite && !busy && (
          <label className="ml-auto cursor-pointer rounded-md border border-slate-300 px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50">
            {has ? 'Replace data' : 'Upload data (.xlsx, .sav, .csv)'}
            <input type="file" accept=".xlsx,.sav,.csv" className="sr-only" aria-label="Project data file"
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void upload(f) }} />
          </label>
        )}
        {busy && (
          <span role="status" className="ml-auto text-slate-600">
            {state === 'uploading' ? `Uploading… ${pct}%` : 'Reading the file and its variables…'}
          </span>
        )}
      </div>
      {error && <p className="mt-2 text-sm text-amber-800">{error}</p>}
      {state === 'failed' && (
        <p className="mt-2 text-sm text-amber-800">
          The file could not be read. Use the respondent-level export (one row per respondent) and try again.
        </p>
      )}
    </section>
  )
}
