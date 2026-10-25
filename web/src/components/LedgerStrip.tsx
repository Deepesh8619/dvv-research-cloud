import { useEffect, useId, useState } from 'react'
import { errorMessage, ledgerApi, type LedgerState } from '../api/client'
import InfoTip from './ui/InfoTip'

const day = (iso: string) =>
  new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })

/** The study's ledger state, and a way to ask "did this study issue this file?". */
export default function LedgerStrip({ studyId }: { studyId: string }) {
  const [state, setState] = useState<LedgerState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [answer, setAnswer] = useState<string | null>(null)
  const inputId = useId()

  useEffect(() => {
    ledgerApi
      .verify(studyId)
      .then((r) => setState(r.data))
      .catch((e) => setError(errorMessage(e) ?? 'Could not check the ledger.'))
  }, [studyId])

  const check = async (file: File | undefined) => {
    if (!file) return
    setAnswer(null)
    try {
      const { data } = await ledgerApi.checkFile(studyId, file)
      setAnswer(
        data.issued && data.at
          ? `This file was issued on ${day(data.at)}`
          : 'This file was not issued by this study',
      )
    } catch (e) {
      setAnswer(errorMessage(e) ?? 'Could not check that file.')
    }
  }

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-panel p-4 max-w-2xl mb-4">
      <p className="text-sm text-slate-800" role="status">
        {error ? (
          error
        ) : !state ? (
          'Checking the ledger…'
        ) : state.ok ? (
          <>
            Ledger: {state.n} entries · intact <span aria-hidden>✓</span>
            {state.head && <> · fingerprint {state.head.slice(0, 6)}…</>}
          </>
        ) : (
          <span className="text-rose-700 font-medium">
            Ledger broken at entry {state.first_bad_seq}
          </span>
        )}
        <InfoTip id="ledger" />
      </p>
      <div className="mt-3">
        <label htmlFor={inputId} className="text-xs font-medium text-slate-600 block mb-1">
          Check a file you received
        </label>
        <input
          id={inputId}
          type="file"
          onChange={(e) => check(e.target.files?.[0])}
          className="text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
        />
        {answer && (
          <p className="text-sm text-slate-800 mt-2" role="status">
            {answer}
          </p>
        )}
      </div>
    </div>
  )
}
