import type { Decoding } from '../../api/client'

/**
 * How this questionnaire was read, for the WHOLE document: which reading skills
 * decoded it, which questions none could read, and the conventions it met
 * repeatedly without understanding. An unfamiliar format is announced here,
 * instead of being discovered one blank-looking question at a time.
 */
const KIND_HELP: Record<string, string> = {
  field_alias: 'a table row label (teach which field it fills)',
  type_signal: 'a note that says what kind of question this is',
  option_tag: 'an answer tag (terminate / exclusive / anchor)',
  randomize_signal: 'an order instruction',
  hidden_variable: 'a computed variable, never asked',
}

function QidLinks({ qids, onGoTo }: { qids: string[]; onGoTo: (qid: string) => void }) {
  return (
    <>
      {qids.map((qid) => (
        <button key={qid} className="font-mono text-xs underline mr-1 hover:text-slate-900" onClick={() => onGoTo(qid)}>
          {qid}
        </button>
      ))}
    </>
  )
}

export default function DecodingPanel({
  decoding,
  onGoTo,
}: {
  decoding: Decoding | null | undefined
  onGoTo: (qid: string) => void
}) {
  if (!decoding) return null
  const unfamiliar = decoding.verdict === 'unfamiliar'
  return (
    <details open={unfamiliar} className="bg-white border border-slate-200 rounded-md p-4 mb-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-800">
        How this questionnaire was read
        <span className={`ml-2 text-xs font-normal ${unfamiliar ? 'text-amber-700' : 'text-slate-500'}`}>
          {decoding.read_by_skills} of {decoding.questions} questions read by known skills
          {unfamiliar ? ' — unfamiliar format, check the list below' : ''}
        </span>
      </summary>
      <div className="mt-3 space-y-4 text-sm text-slate-700">
        <section>
          <h4 className="font-medium">Skills used</h4>
          <ul className="mt-1 flex flex-wrap gap-2">
            {decoding.skills.map((s) => (
              <li key={s.key} className="rounded bg-slate-100 px-2 py-0.5 text-xs">
                {s.name} · {s.questions}
              </li>
            ))}
          </ul>
        </section>
        {decoding.not_read.length > 0 && (
          <section>
            <h4 className="font-medium text-amber-800">No skill could read these</h4>
            <p className="text-xs text-slate-500">
              Open each one: re-read it, ask the question agent, or correct it by hand.
            </p>
            <div className="mt-1">
              <QidLinks qids={decoding.not_read} onGoTo={onGoTo} />
            </div>
          </section>
        )}
        {decoding.teach.length > 0 && (
          <section>
            <h4 className="font-medium">Met repeatedly, not understood — teach once</h4>
            <p className="text-xs text-slate-500">
              Open a listed question, find the fragment under “What the parser read”, and press “Teach this”. Every later
              document from this client is then read with it.
            </p>
            <ul className="mt-1 space-y-2">
              {decoding.teach.map((t) => (
                <li key={t.fragment}>
                  <code className="text-xs bg-slate-50 px-1">{t.fragment}</code>
                  <span className="text-xs text-slate-500">
                    {' '}
                    on {t.questions} questions · {KIND_HELP[t.suggest] ?? t.suggest} ·{' '}
                  </span>
                  <QidLinks qids={t.examples} onGoTo={onGoTo} />
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </details>
  )
}
