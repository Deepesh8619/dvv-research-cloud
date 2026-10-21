import { useCallback, useEffect, useState } from 'react'

import { downloadFile, questionnaireApi, type TranslationStatus } from '../api/client'

/**
 * Languages the survey can be fielded in. The translator works in a sheet —
 * one row per thing a respondent reads — and it comes back checked: nothing
 * blank, every piping token ([INSERT PROVIDER], {Q1}) kept exactly.
 */
const LANG = /^[a-z]{2,3}(-[A-Za-z]{2,4})?$/

export default function TranslationsPanel({ studyId }: { studyId: string }) {
  const [nStrings, setNStrings] = useState<number | null>(null)
  const [languages, setLanguages] = useState<TranslationStatus[]>([])
  const [lang, setLang] = useState('')
  const [problems, setProblems] = useState<Array<{ key: string; problem: string }> | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    questionnaireApi.translations(studyId)
      .then((r) => { setNStrings(r.data.n_strings); setLanguages(r.data.languages) })
      .catch(() => setNStrings(null))
  }, [studyId])
  useEffect(load, [load])

  if (nStrings === null) return null
  const ok = LANG.test(lang)

  const upload = (file: File) => {
    setError(null)
    questionnaireApi.uploadTranslation(studyId, lang, file)
      .then((r) => { setProblems(r.data.problems); load() })
      .catch((e) => setError(e?.response?.data?.detail ?? 'Could not read that sheet.'))
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-panel" data-testid="translations">
      <h2 className="font-semibold text-slate-900">Translations</h2>
      <p className="mt-1 max-w-2xl text-sm text-slate-500">
        {nStrings} strings a respondent reads. A link opened after the upload serves a language with
        <code className="mx-1 rounded bg-slate-100 px-1">?lang=hi</code>on its address; the data records which one each
        respondent read.
      </p>
      {languages.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {languages.map((l) => (
            <li key={l.lang} className="flex flex-wrap items-center gap-2">
              <span className="font-mono font-medium text-slate-900">{l.lang}</span>
              <span className="text-slate-600">{l.n_translated} of {l.n_total} translated</span>
              {l.n_problems > 0 && <span className="text-amber-700">· {l.n_problems} to fix</span>}
              <button type="button" className="text-primary-500 hover:text-primary-600"
                onClick={() => setLang(l.lang)}>select</button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-600">Language code
          <input className="block w-28 rounded-md border border-slate-300 px-2 py-1 text-sm" value={lang}
            placeholder="hi" onChange={(e) => setLang(e.target.value.trim())} />
        </label>
        <button type="button" disabled={!ok}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
          onClick={() => downloadFile(`/studies/${studyId}/questionnaire/translations/${lang}.csv`, `translation_${lang}.csv`)}>
          Download sheet
        </button>
        <label className={`rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white ${ok ? 'cursor-pointer' : 'pointer-events-none opacity-40'}`}>
          Upload filled sheet
          <input type="file" accept=".csv,text/csv" className="sr-only" disabled={!ok}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
        </label>
      </div>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      {problems && (
        problems.length === 0
          ? <p className="mt-3 text-sm text-emerald-700">Every string translated, every token kept.</p>
          : (
            <ul className="mt-3 max-h-48 space-y-1 overflow-y-auto text-sm text-amber-800">
              {problems.slice(0, 50).map((p) => <li key={p.key}><span className="font-mono">{p.key}</span>: {p.problem}</li>)}
            </ul>
          )
      )}
    </section>
  )
}
