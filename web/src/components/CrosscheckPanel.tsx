import { useEffect, useState } from 'react'
import axios from 'axios'

import { CrosscheckReport, QuestionnaireStatus, questionnaireApi, studiesApi } from '../api/client'

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
  terminate_violated: 'Screened out, but kept answering',
  terminate_recorded: 'Screened out and stopped there, as scripted',
  type_mismatch: 'Read as a coded question, but the data says otherwise',
  option_not_scripted: 'Option in the questionnaire with no punch in the data',
  punch_not_in_questionnaire: 'Punch in the data the questionnaire never offers',
  mask_violated: 'Answer outside the masked option list',
  pipe_source_empty: 'Piped text with nothing to pipe in',
  coded_in_companion: 'Open response — its codes live in a derived variable',
  exclusive_violated: 'An exclusive answer (e.g. "None of these") ticked with others',
  too_many_answers: 'More answers than the question allows',
  sum_not_total: 'Number boxes that do not add up to the stated total',
}

const CHECK_LABELS: Record<string, string> = {
  codes: 'answer codes',
  single: 'single-answer questions',
  range: 'numeric ranges',
  terminate: 'screen-outs',
  mask: 'masked option lists',
}

export default function CrosscheckPanel({
  studyId,
  dataFilename,
}: {
  studyId: string
  dataFilename?: string | null
}) {
  const [report, setReport] = useState<CrosscheckReport | null>(null)
  const [questionnaire, setQuestionnaire] = useState<QuestionnaireStatus | null>(null)
  // An `info` finding is a check that PASSED and is worth showing — "the
  // screen-out held". Counting it as a discrepancy would report a clean file
  // as a dirty one.
  const problems = (report?.findings ?? []).filter((f) => f.severity !== 'info')
  const [running, setRunning] = useState(false)
  // The data file shown and checked. A project started from its questionnaire
  // had no data and no way to add it here, so the check could never run.
  const [dataName, setDataName] = useState<string | null>(dataFilename ?? null)
  const [dataState, setDataState] = useState<'idle' | 'uploading' | 'processing' | 'ready' | 'failed'>('idle')
  const [dataPct, setDataPct] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // The last result is stored on the study, and was never read back — so
  // leaving the project and returning showed an empty panel as though the
  // check had never run. The work was done, paid for, then hidden.
  useEffect(() => {
    let cancelled = false
    questionnaireApi
      .status(studyId)
      .then((res) => {
        if (!cancelled) setQuestionnaire(res.data)
      })
      .catch(() => {
        /* none uploaded yet — the strip below just says so */
      })
    studiesApi
      .storedCrosscheck(studyId)
      .then((res) => {
        if (!cancelled && res.data.report) setReport(res.data.report)
      })
      .catch(() => {
        /* never run, or unreadable — the button is still there */
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [studyId])

  const attach = async (file: File) => {
    setError(null)
    setDataState('uploading')
    setDataPct(0)
    try {
      await studiesApi.attachData(studyId, file, setDataPct)
      setDataName(file.name)
      setDataState('processing')
      // Reading the file builds its data map; the check needs it. Wait for the
      // project to leave "classifying" (a few seconds for most files).
      for (let i = 0; i < 100; i++) {
        await new Promise((r) => setTimeout(r, 3000))
        const status = (await studiesApi.get(studyId)).data.status
        if (status === 'error') {
          setDataState('failed')
          return
        }
        if (status !== 'classifying') {
          setDataState('ready')
          return
        }
      }
      setDataState('failed')
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : null
      setError(typeof detail === 'string' ? detail : 'The data file could not be uploaded.')
      setDataState('idle')
    }
  }

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
    <section className="bg-white rounded-lg border border-slate-200 shadow-panel p-6">
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

      {/* Which files this wave actually ran on, and a way to get them back.
          Both are served from the database, so they outlive the restart that
          wipes the disk — which is exactly when someone needs to check what
          the client sent. */}
      <dl className="mt-4 grid gap-x-6 gap-y-2 sm:grid-cols-2 text-sm">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <dt className="text-slate-500">Questionnaire</dt>
          {questionnaire?.source_filename ? (
            <>
              <dd className="font-medium text-slate-800">
                {questionnaire.source_filename}
              </dd>
              <span className="text-xs text-slate-500">
                v{questionnaire.version}
                {questionnaire.created_at
                  ? ` · ${new Date(questionnaire.created_at).toLocaleDateString()}`
                  : ''}
              </span>
              <button
                type="button"
                onClick={() =>
                  questionnaireApi.downloadSource(studyId, questionnaire.source_filename)
                }
                className="text-xs font-medium text-primary-600 hover:underline"
              >
                Download
              </button>
              {/* The instrument as a workbook: questions, answers, routing,
                  the checks and the flow. For the people who need it off this
                  screen — a scripter in another tool, a client signing off, a
                  PM setting this wave beside the last one. */}
              <button
                type="button"
                onClick={() =>
                  questionnaireApi.downloadWorkbook(
                    studyId,
                    `${questionnaire.source_filename.replace(/\.[^.]+$/, '')}_questionnaire.xlsx`,
                  )
                }
                className="text-xs font-medium text-primary-600 hover:underline"
              >
                Analysis (.xlsx)
              </button>
            </>
          ) : (
            <dd className="text-slate-400">None uploaded</dd>
          )}
        </div>
        <div className="flex flex-wrap items-baseline gap-x-2">
          <dt className="text-slate-500">Data file</dt>
          {dataName ? (
            <>
              <dd className="font-medium text-slate-800">{dataName}</dd>
              {dataState === 'idle' && (
                <button
                  type="button"
                  onClick={() => studiesApi.download(studyId, 'source', dataName)}
                  className="text-xs font-medium text-primary-600 hover:underline"
                >
                  Download
                </button>
              )}
            </>
          ) : (
            <dd className="text-slate-400">None uploaded</dd>
          )}
          {dataState !== 'uploading' && dataState !== 'processing' && (
            <label className="text-xs font-medium text-primary-600 hover:underline cursor-pointer">
              {dataName ? 'Replace' : 'Upload data file (.xlsx, .sav, .csv)'}
              <input
                id="crosscheck-data-file"
                type="file"
                accept=".xlsx,.sav,.csv"
                className="sr-only"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  e.target.value = ''
                  if (f) void attach(f)
                }}
              />
            </label>
          )}
        </div>
      </dl>

      {dataState !== 'idle' && (
        <p
          role="status"
          className={`text-sm rounded-md px-3 py-2 mt-4 border ${
            dataState === 'failed'
              ? 'text-amber-800 bg-amber-50 border-amber-200'
              : dataState === 'ready'
                ? 'text-emerald-800 bg-emerald-50 border-emerald-200'
                : 'text-slate-700 bg-slate-50 border-slate-200'
          }`}
        >
          {dataState === 'uploading' && `Uploading the data file… ${dataPct}%`}
          {dataState === 'processing' && 'Reading the data file and its variables… this usually takes under a minute.'}
          {dataState === 'ready' && 'Data file ready. Run the cross-check to compare it with the questionnaire.'}
          {dataState === 'failed' && 'The data file could not be read. Check that it is the respondent-level export (one row per respondent) and try again.'}
        </p>
      )}

      {report?.data_file_available === false && (
        <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 mt-4">
          The respondent rows are no longer on disk (the server restarted), so the
          row-level checks below were skipped. Everything that compares the
          questionnaire against the data map still ran. Use Re-process to rebuild the
          data from the archived upload, then run this again.
        </p>
      )}

      {loading && !report && (
        <p className="text-sm text-slate-400 mt-4">Loading the last result…</p>
      )}

      {report && (
        <div className="mt-5 space-y-4">
          {report.run_at && (
            <p className="text-xs text-slate-500">
              Last run {new Date(report.run_at).toLocaleString()}
              {report.run_by ? ` by ${report.run_by}` : ''}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
            <span
              className={`font-medium ${report.passed ? 'text-emerald-700' : 'text-red-700'}`}
            >
              {problems.length === 0
                ? 'No discrepancies found'
                : `${problems.length} discrepanc${problems.length === 1 ? 'y' : 'ies'}`}
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
                      f.severity === 'error'
                        ? 'bg-red-500'
                        : f.severity === 'info'
                          ? 'bg-slate-300'
                          : 'bg-amber-400'
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

          {/* Questions rebuilt from the data map. Named explicitly: their
              wording comes from the script rather than the client's document,
              and the two can legitimately differ. */}
          {report.reconciliation && report.reconciliation.recovered_from_datamap.length > 0 && (
            <p className="text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded px-2.5 py-2">
              <span className="font-medium">
                {report.reconciliation.recovered_from_datamap.length} questions were rebuilt
                from the data map
              </span>{' '}
              because the document reader did not find them. Their wording and options come
              from the script as fielded, so they are checked here — but review them against
              the questionnaire before relying on the text.
            </p>
          )}

          {/* Matched on question text because the names differed. Surfaced,
              never quietly accepted: a wrong match silently checks the wrong
              column, and only a human can settle it. */}
          {report.needs_confirmation && report.needs_confirmation.length > 0 && (
            <div className="border border-sky-200 bg-sky-50/60 rounded-md px-3 py-2.5">
              <p className="text-xs font-semibold uppercase tracking-wide text-sky-900">
                Please confirm {report.needs_confirmation.length} match
                {report.needs_confirmation.length === 1 ? '' : 'es'}
              </p>
              <p className="text-xs text-slate-600 mt-0.5">
                The variable name differs from the question id, so these were matched on
                wording. Check each one is the same question.
              </p>
              <ul className="mt-2 space-y-1.5">
                {report.needs_confirmation.map((m) => (
                  <li key={m.qid} className="text-xs text-slate-700">
                    <span className="font-mono">{m.qid}</span>
                    <span className="text-slate-400"> ↔ </span>
                    <span className="font-mono">{m.variables.join(', ')}</span>
                    <span className="text-slate-400"> ({Math.round(m.confidence * 100)}%)</span>
                    <span className="block text-slate-500 mt-0.5">
                      “{m.question_text}” vs “{m.variable_label}”
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Who each question was actually asked of, against who it was
              supposed to be asked of. A base that never held is a hole in the
              sample, and nothing in the raw data looks wrong. */}
          {report.routing && (report.routing.issues.length > 0 || report.routing.n_checked > 0) && (
            <div className="border border-slate-200 rounded-md px-3 py-2.5">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-700">
                Bases checked against who answered
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                {report.routing.n_checked} verified against the data
                {report.routing.n_unchecked > 0
                  ? ` · ${report.routing.n_unchecked} could not be checked`
                  : ''}
              </p>
              {report.routing.issues.length > 0 && (
                <ul className="mt-1.5 space-y-1">
                  {report.routing.issues.map((r) => (
                    <li key={r.qid} className="text-sm text-slate-700">
                      <span className="font-mono text-xs text-slate-600">{r.qid}</span>
                      {r.checked ? (
                        <span className="text-slate-500"> · {r.agreement_pct}% agree</span>
                      ) : null}{' '}
                      — {r.detail}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Conditions that cannot fire. Reported BEFORE the data findings:
              a dead screen-out shows zero violations, which reads exactly like
              a screen-out nobody breached. */}
          {report.conditions && report.conditions.issues.length > 0 && (
            <div className="border border-amber-200 bg-amber-50/60 rounded-md px-3 py-2.5">
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-900">
                Conditions that cannot do what they say
              </p>
              <ul className="mt-1.5 space-y-1">
                {report.conditions.issues.map((i, n) => (
                  <li key={`${i.qid}-${i.kind}-${n}`} className="text-sm text-slate-700">
                    <span className="font-mono text-xs text-slate-600">{i.qid}</span>{' '}
                    <span className="text-slate-500">· {i.where}</span> — {i.detail}
                  </li>
                ))}
              </ul>
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
