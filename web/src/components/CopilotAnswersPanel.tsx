import { useEffect, useState } from 'react'
import {
  copilotApi,
  CopilotAnswerRecord,
  CopilotEvalRun,
  errorMessage,
  FlaggedCopilotAnswer,
} from '../api/client'

type Show = 'all' | 'flagged' | 'errors' | 'slow'
const SHOWS: Array<{ key: Show; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'flagged', label: 'Flagged wrong' },
  { key: 'errors', label: 'A tool refused' },
  { key: 'slow', label: 'Slower than 20 s' },
]

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`

/** The eval-set entry this answer should become (evals/copilot/cases.json); expectations are filled by hand. */
const draftCase = (f: FlaggedCopilotAnswer) =>
  JSON.stringify(
    {
      id: `fb-${f.conversation_id.replace(/^conv_/, '')}-${f.seq}`,
      category: 'feedback',
      study: f.study_id,
      why: f.note || 'flagged as wrong',
      turns: [{ say: f.question, expect: { tools_any: f.lookups.map((l) => l.tool) } }],
    },
    null,
    1,
  )

const SUITE_LABEL: Record<string, string> = {
  synthetic: 'Copilot eval (synthetic project)',
  gold: 'Gold set (your real projects)',
}

/** One suite's score over time: the line every change to the Copilot is held to. */
function SuiteHistory({ runs }: { runs: CopilotEvalRun[] }) {
  const latest = runs[0]
  const oldestFirst = [...runs].reverse()
  const suite = latest.suite ?? 'synthetic'
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4" data-testid={`copilot-evals-${suite}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{SUITE_LABEL[suite] ?? suite}</p>
        <p className="text-xs text-slate-400">
          last run {new Date(latest.at).toLocaleString()} · commit {latest.commit}
        </p>
      </div>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">
        {latest.score}%{' '}
        <span className="text-sm font-normal text-slate-500">
          ({latest.passed}/{latest.cases} {suite === 'gold' ? 'passed every repeat' : 'questions'})
        </span>
      </p>
      <div className="mt-3 flex h-12 items-end gap-1" aria-label="Score per run, oldest first">
        {oldestFirst.map((r, i) => (
          <div key={`${r.at}-${i}`} title={`${r.score}% · ${new Date(r.at).toLocaleDateString()} · ${r.commit}`}
            className={`w-3 rounded-sm ${r.score >= 95 ? 'bg-emerald-500' : r.score >= 90 ? 'bg-amber-400' : 'bg-red-400'}`}
            style={{ height: `${Math.max(8, (r.score - 70) * 1.6)}%` }} />
        ))}
      </div>
      {suite === 'gold' && Object.keys(latest.by_category).length > 0 && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
          {Object.entries(latest.by_category).map(([skill, score]) => (
            <div key={skill} className="flex justify-between gap-2">
              <dt className="text-slate-500">{skill}</dt>
              <dd className="tabular-nums text-slate-800">{score}</dd>
            </div>
          ))}
        </dl>
      )}
      {latest.failed.length > 0 && (
        <p className="mt-2 text-xs text-slate-600">
          Failed last run: <span className="font-mono">{latest.failed.join(', ')}</span>
        </p>
      )}
      {(latest.flaky ?? []).length > 0 && (
        <p className="mt-1 text-xs text-amber-700">
          Unstable (passed on some repeats only): <span className="font-mono">{(latest.flaky ?? []).join(', ')}</span>
        </p>
      )}
    </section>
  )
}

/** Every suite's history: the invented project, and the private gold set of real ones. */
function EvalHistory() {
  const [runs, setRuns] = useState<CopilotEvalRun[] | null>(null)
  useEffect(() => {
    copilotApi.evals().then((res) => setRuns(res.data)).catch(() => setRuns([]))
  }, [])
  if (!runs) return null
  if (runs.length === 0)
    return (
      <p className="text-sm text-slate-500">
        No eval runs recorded yet. Run <code className="rounded bg-slate-100 px-1">./dvv eval</code> on the
        server, or <code className="rounded bg-slate-100 px-1">./dvv eval-nightly on</code> to run it every night.
      </p>
    )
  const suites = ['gold', 'synthetic'].map((s) => runs.filter((r) => (r.suite ?? 'synthetic') === s))
    .filter((rs) => rs.length > 0)
  return <div className="space-y-3">{suites.map((rs) => <SuiteHistory key={rs[0].suite ?? 'synthetic'} runs={rs} />)}</div>
}

/** One answer: what was asked, what came back, and every step that made it. */
function Answer({ a }: { a: CopilotAnswerRecord }) {
  const [copied, setCopied] = useState(false)
  const failedSteps = a.trace?.steps.filter((s) => !s.ok) ?? []
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(draftCase(a))
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs text-slate-500">
        <span>
          {a.user ?? 'someone'}
          {a.study_id ? ` · ${a.study_id}` : ''} · {new Date(a.at).toLocaleString()}
          {a.trace ? ` · ${seconds(a.trace.ms)} · ${a.trace.steps.length} steps` : ''}
        </span>
        <span className="flex gap-3">
          {a.flagged && <span className="font-medium text-amber-700">Flagged wrong</span>}
          {failedSteps.length > 0 && <span className="font-medium text-red-700">{failedSteps.length} refused</span>}
          <button type="button" onClick={copy} className="font-medium text-primary-600 hover:underline">
            {copied ? 'Copied' : 'Copy as test case'}
          </button>
        </span>
      </div>
      <p className="mt-2 font-medium text-slate-900">{a.question}</p>
      {a.note && <p className="mt-1 text-amber-800">“{a.note}”</p>}
      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-slate-500">
          How it was answered{a.answered_by ? ` · ${a.answered_by}` : ''}
          {a.trace ? ` · ${a.trace.model_calls} model calls · ${a.trace.tokens_in.toLocaleString()} tokens in` : ''}
        </summary>
        {a.trace && a.trace.steps.length > 0 ? (
          <ol className="mt-2 space-y-1 text-xs">
            {a.trace.steps.map((s, i) => (
              <li key={i} className={s.ok ? 'text-slate-600' : 'text-red-700'}>
                <span className="font-mono">{s.tool}</span> · {seconds(s.ms)} · {s.chars.toLocaleString()} chars
                {s.error && <span> · refused: {s.error}</span>}
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-2 text-xs text-slate-500">
            {a.trace ? 'Answered without looking anything up.' : 'Answered before steps were recorded.'}
          </p>
        )}
        <p className="mt-2 whitespace-pre-wrap text-slate-700">{a.reply}</p>
      </details>
    </li>
  )
}

/** Admin → Copilot answers: the eval score, then recent answers and how each was made. */
export default function CopilotAnswersPanel() {
  const [show, setShow] = useState<Show>('flagged')
  const [items, setItems] = useState<CopilotAnswerRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setItems(null)
    setError(null)
    copilotApi
      .answers(show)
      .then((res) => setItems(res.data))
      .catch((err) => setError(errorMessage(err) || 'Could not load the Copilot answers.'))
  }, [show])

  return (
    <div className="space-y-4">
      <EvalHistory />
      <nav className="flex flex-wrap gap-2" aria-label="Which answers">
        {SHOWS.map((s) => (
          <button key={s.key} type="button" onClick={() => setShow(s.key)} aria-pressed={show === s.key}
            className={`rounded-full border px-3 py-1 text-xs font-medium ${show === s.key
              ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300 text-slate-600 hover:border-slate-500'}`}>
            {s.label}
          </button>
        ))}
      </nav>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {!error && !items && <p className="text-sm text-slate-500">Loading…</p>}
      {items && items.length === 0 && (
        <p className="text-sm text-slate-500">
          {show === 'flagged'
            ? 'No answers flagged yet. Anyone can flag a Copilot reply with "Wrong answer?" under it.'
            : 'No answers match.'}
        </p>
      )}
      {items && items.length > 0 && (
        <ul className="space-y-3" data-testid="copilot-answers">
          {items.map((a) => <Answer key={`${a.conversation_id}-${a.seq}`} a={a} />)}
        </ul>
      )}
    </div>
  )
}
