import type { BriefChecklist, Decoding } from '../../api/client'

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
  by_hand: 'a rule for this question — set it on the question or ask the question agent',
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

const KIND_LABEL: Record<string, string> = {
  quota: 'Quota',
  screening: 'Screening',
  variable: 'Variable to create',
  note: 'Programming note',
}
const STATUS_STYLE: Record<string, string> = {
  covered: 'text-emerald-700',
  missing: 'text-red-700 font-medium',
  check: 'text-amber-700',
}

/** The brief, read first: each need it stated, met or not, and where. */
function BriefSection({ brief, onGoTo }: { brief: BriefChecklist; onGoTo: (qid: string) => void }) {
  const families = Object.entries(brief.conventions.label_families)
  const markers = Object.keys(brief.conventions.pn_markers)
  // Missing first, then the ones a person has to confirm.
  const order = { missing: 0, check: 1, covered: 2 }
  const items = [...brief.items].sort((a, b) => order[a.status] - order[b.status])
  return (
    <section>
      <h4 className="font-medium">What the document asks for</h4>
      <p className="text-xs text-slate-500">
        {brief.covered} met · {brief.missing} missing · {brief.check} to confirm
        {brief.screens.length > 0 && <> · {brief.screens.length} information screens</>}
      </p>
      {(families.length > 0 || markers.length > 0) && (
        <p className="mt-1 text-xs text-slate-500">
          Labels: {families.map(([p, n]) => `${p} (${n})`).join(', ') || '—'}
          {markers.length > 0 && <> · scripter notes marked {markers.join(', ')}</>}
        </p>
      )}
      {items.length > 0 && (
        <ul className="mt-2 space-y-1">
          {items.map((item, i) => (
            <li key={i} className="text-xs">
              <span className={STATUS_STYLE[item.status]}>{item.status}</span>
              <span className="text-slate-500"> · {KIND_LABEL[item.kind] ?? item.kind} · </span>
              {item.text}
              <span className="text-slate-500"> — {item.why} </span>
              <QidLinks qids={item.where} onGoTo={onGoTo} />
            </li>
          ))}
        </ul>
      )}
    </section>
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
    <details open={unfamiliar || (decoding.brief?.missing ?? 0) > 0} className="bg-white border border-slate-200 rounded-md p-4 mb-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-800">
        How this questionnaire was read
        <span className={`ml-2 text-xs font-normal ${unfamiliar ? 'text-amber-700' : 'text-slate-500'}`}>
          {decoding.read_by_skills} of {decoding.questions} questions read by known skills
          {unfamiliar ? ' — unfamiliar format, check the list below' : ''}
        </span>
      </summary>
      <div className="mt-3 space-y-4 text-sm text-slate-700">
        {decoding.brief && <BriefSection brief={decoding.brief} onGoTo={onGoTo} />}
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
