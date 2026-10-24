import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { studiesApi, type FieldedData } from '../api/client'
import FieldworkCards from './FieldworkCards'
import InfoTip from './ui/InfoTip'

/**
 * Fielded data that arrived as a file. A study fielded on another platform
 * (Decipher, Qualtrics…) comes back as an export, and the field monitor only
 * knew about links run here — so finished fieldwork looked like none at all.
 * This shows the export as the survey's fieldwork: who is in it, how each
 * interview ended, and the way on to checking it against the questionnaire.
 */
export default function FieldedDataPanel({ studyId }: { studyId: string }) {
  const [data, setData] = useState<FieldedData | null>(null)

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

  const picker = (
    <Link to={`/projects/${studyId}`} className="text-sm font-medium text-primary-600 hover:underline">
      {data?.available ? 'Replace data on the project page' : 'Upload data on the project page'}
    </Link>
  )

  return (
    <>
    <section className="mb-6 rounded-lg border border-slate-200 bg-white p-5 shadow-panel" data-testid="fielded-data">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold text-slate-900">Fielded data<InfoTip id="fielded_data" /></h2>
          <p className="mt-1 max-w-xl text-sm text-slate-500">
            {data?.available
              ? 'The respondent data in this project. Fielded on another platform? It is your fieldwork all the same.'
              : 'Fielded this survey on another platform? Upload its export as the project’s data and it becomes this survey’s fieldwork.'}
          </p>
        </div>
        {picker}
      </div>


      {data?.available && (
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
          {!data.status_variable && (
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
    {data?.available && (
      <div className="mb-6">
        <FieldworkCards key={data.updated_at ?? ''} studyId={studyId} ready full />
      </div>
    )}
    </>
  )
}
