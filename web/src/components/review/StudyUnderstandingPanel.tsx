import type { StudyUnderstanding } from '../../api/client'

/**
 * What the study's instructions say, read before any question — the first
 * thing an analyst takes in. Every item quotes the instruction line it rests
 * on, so a reviewer can tell understanding from invention at a glance.
 */
function Quote({ text }: { text: string }) {
  return <q className="block text-xs text-slate-500 italic mt-0.5">{text}</q>
}

function QidLinks({ qids, onGoTo }: { qids?: string[]; onGoTo: (qid: string) => void }) {
  if (!qids?.length) return null
  return (
    <span className="ml-1">
      {qids.map((qid) => (
        <button key={qid} className="font-mono text-xs underline mr-1 hover:text-slate-900" onClick={() => onGoTo(qid)}>
          {qid}
        </button>
      ))}
    </span>
  )
}

export default function StudyUnderstandingPanel({
  understanding,
  onGoTo,
}: {
  understanding: StudyUnderstanding | null | undefined
  onGoTo: (qid: string) => void
}) {
  if (!understanding) return null
  if (understanding.unavailable) {
    return (
      <div className="bg-amber-50 border border-amber-200 rounded-md p-3 mb-4 text-sm text-amber-800">
        The study's instructions could not be read this time (no language model was reachable), so
        questions were read without them. Re-extracting retries.
      </div>
    )
  }
  const { groups = [], rules = [], hidden_variables = [], concerns = [] } = understanding
  return (
    <details open className="bg-white border border-slate-200 rounded-md p-4 mb-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-800">
        What this survey is about
      </summary>
      <div className="mt-3 space-y-4 text-sm text-slate-700">
        {understanding.summary && <p>{understanding.summary}</p>}
        {understanding.audience && (
          <p>
            <span className="font-medium">Who qualifies: </span>
            {understanding.audience}
          </p>
        )}

        {concerns.length > 0 && (
          <section>
            <h4 className="font-medium text-red-700">Raise with the client</h4>
            <ul className="mt-1 space-y-2">
              {concerns.map((c, i) => (
                <li key={i}>
                  {c.issue}
                  <Quote text={c.evidence} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {rules.length > 0 && (
          <section>
            <h4 className="font-medium">Instructions that govern questions</h4>
            <ul className="mt-1 space-y-2">
              {rules.map((r, i) => (
                <li key={i}>
                  {r.meaning}
                  <QidLinks qids={r.applies_to} onGoTo={onGoTo} />
                  <Quote text={r.evidence} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {groups.length > 0 && (
          <section>
            <h4 className="font-medium">Sample groups</h4>
            <ul className="mt-1 space-y-2">
              {groups.map((g, i) => (
                <li key={i}>
                  <span className="font-medium">{g.name}</span>
                  {g.definition && <> — {g.definition}</>}
                  {g.target && <span className="text-slate-500"> ({g.target})</span>}
                  <QidLinks qids={g.measured_by} onGoTo={onGoTo} />
                  <Quote text={g.evidence} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {hidden_variables.length > 0 && (
          <section>
            <h4 className="font-medium">Classifications to compute</h4>
            <ul className="mt-1 space-y-2">
              {hidden_variables.map((h, i) => (
                <li key={i}>
                  <span className="font-mono text-xs">{h.name}</span> — {h.meaning}
                  <QidLinks qids={h.built_from} onGoTo={onGoTo} />
                  <Quote text={h.evidence} />
                </li>
              ))}
            </ul>
          </section>
        )}

        <p className="text-xs text-slate-400">
          Read by {understanding.model ?? 'a language model'} before the questions.
          {(understanding.claims_dropped_unsupported ?? 0) > 0 &&
            ` ${understanding.claims_dropped_unsupported} statement(s) it made were dropped because the instructions do not say them.`}
        </p>
      </div>
    </details>
  )
}
