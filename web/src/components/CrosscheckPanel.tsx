import { useState } from 'react'
import axios from 'axios'

import { CrosscheckReport, studiesApi } from '../api/client'

/**
 * Check the collected data against the questionnaire it was collected under.
 *
 * The standard pipeline validates a data file on its own terms — speeders,
 * duplicates, straightliners. None of that says whether the data matches the
 * instrument the client signed off, which is the comparison a DP shop is
 * actually paid for.
 *
 * It is a button rather than something that happens on upload, on purpose: a
 * project often has data before its questionnaire has been reviewed, and
 * running this against an unreviewed extraction would report the parser's
 * mistakes as the client's.
 */
const KIND_LABELS: Record<string, string> = {
  undeclared_code: 'Answer not in the scripted punch list',
  single_multi: 'More than one answer to a single-answer question',
  out_of_range: 'Number outside the allowed range',
  terminate_violated: 'Respondent who should have been screened out',
  type_mismatch: 'Read as a coded question, but the data says otherwise',
  option_not_scripted: 'Option in the questionnaire with no punch in the data',
  punch_not_in_questionnaire: 'Punch in the data the questionnaire never offers',
  mask_violated: 'Answer outside the masked option list',
}

const CHECK_LABELS: Record<string, string> = {
  codes: 'answer codes',
  single: 'single-answer questions',
  range: 'numeric ranges',
  terminate: 'screen-outs',
  mask: 'masked option lists',
}

export default function CrosscheckPanel({ studyId }: { studyId: string }) {
  const [report, setReport] = useState<CrosscheckReport | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      const res = await studiesApi.crosscheck(studyId)
      setReport(res.data)
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
      setError(typeof detail === 'string' ? detail : 'The cross-check could not run.')
    } finally {
      setRunning(false)
    }
  }

  return (
    <section className="bg-white rounded-lg border border-slate-200 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold text-slate-900">Data vs questionnaire</h2>
          <p className="text-sm text-slate-500 mt-1 max-w-xl">
            Checks every answer against what the questionnaire declared — codes,
            screen-outs, single answers and numeric ranges. No AI is involved,
            so every finding can be opened and seen in the file.
          </p>
        </div>
        <button
          onClick={run}
          disabled={running}
          className="text-sm bg-slate-900 text-white font-medium px-3 py-1.5 rounded-md hover:bg-slate-800 disabled:opacity-50 shrink-0"
        >
          {running ? 'Checking…' : report ? 'Check again' : 'Run cross-check'}
        </button>
      </div>

      {error && (
        <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 mt-4">
          {error}
        </p>
      )}

      {report && (
        <div className="mt-5 space-y-4">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
            <span
              className={`font-medium ${report.passed ? 'text-emerald-700' : 'text-red-700'}`}
            >
              {report.passed
                ? 'No discrepancies found'
                : `${report.findings.length} discrepanc${report.findings.length === 1 ? 'y' : 'ies'}`}
            </span>
            <span className="text-slate-500">{report.n_rows.toLocaleString()} respondents</span>
            <span className="text-slate-500">{report.linked_questions} questions linked</span>
          </div>

          {/* What was actually examined. Without this, "nothing found" and
              "nothing looked at" read exactly the same. */}
          {Object.keys(report.checked).length > 0 && (
            <p className="text-xs text-slate-500">
              Checked:{' '}
              {Object.entries(report.checked)
                .map(([k, n]) => `${n} ${CHECK_LABELS[k] ?? k}`)
                .join(' · ')}
            </p>
          )}

          {report.findings.length > 0 && (
            <div className="border border-slate-200 rounded-md overflow-hidden">
              {report.findings.map((f, i) => (
                <div
                  key={`${f.qid}-${f.kind}-${i}`}
                  className="border-b border-slate-100 last:border-b-0 px-3 py-2.5 flex gap-3"
                >
                  <span
                    className={`w-1 rounded-full shrink-0 ${
                      f.severity === 'error' ? 'bg-red-500' : 'bg-amber-400'
                    }`}
                    aria-hidden="true"
                  />
                  <div className="min-w-0">
                    <p className="text-sm text-slate-800">
                      <span className="font-mono text-xs text-slate-600">{f.qid}</span>{' '}
                      <span className="text-slate-500">·</span>{' '}
                      {KIND_LABELS[f.kind] ?? f.kind}
                    </p>
                    <p className="text-sm text-slate-600 mt-0.5">{f.detail}</p>
                    <p className="text-xs text-slate-400 mt-1">
                      {f.n_rows.toLocaleString()} respondent{f.n_rows === 1 ? '' : 's'}
                      {f.example_rows.length > 0 && ` · e.g. rows ${f.example_rows.join(', ')}`}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* What every column in the file is for. Hidden variables carry the
              segment and quota logic a client analyses by, so they are named
              rather than buried in an "unmatched" count. */}
          {report.variable_roles && report.variable_roles.length > 0 && (
            <details className="text-xs text-slate-600">
              <summary className="cursor-pointer text-slate-500 hover:text-slate-700">
                What the {report.variable_roles.reduce((a, r) => a + r.n, 0)} data columns are
              </summary>
              <ul className="mt-2 space-y-0.5">
                {report.variable_roles.map((r) => (
                  <li key={r.role} className="flex gap-2">
                    <span className="font-mono tabular-nums w-10 text-right">{r.n}</span>
                    <span>{r.label}</span>
                  </li>
                ))}
              </ul>
              {report.hidden_variables && report.hidden_variables.length > 0 && (
                <p className="mt-2 text-slate-500">
                  Hidden variables are computed by the script and are meant to be
                  absent from the questionnaire — they are not gaps:{' '}
                  <span className="font-mono">
                    {report.hidden_variables.slice(0, 6).join(', ')}
                    {report.hidden_variables.length > 6 &&
                      ` and ${report.hidden_variables.length - 6} more`}
                  </span>
                </p>
              )}
            </details>
          )}

          {(report.skipped.length > 0 || report.unmatched_questions.length > 0) && (
            <div className="text-xs text-slate-500 space-y-1">
              {report.unmatched_questions.length > 0 && (
                <p>
                  {report.unmatched_questions.length} question
                  {report.unmatched_questions.length === 1 ? '' : 's'} could not be matched to a
                  variable, so nothing was checked for them:{' '}
                  <span className="font-mono">
                    {report.unmatched_questions.slice(0, 8).join(', ')}
                    {report.unmatched_questions.length > 8 && '…'}
                  </span>
                </p>
              )}
              {report.skipped.map((s) => (
                <p key={s}>Skipped — {s}</p>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
