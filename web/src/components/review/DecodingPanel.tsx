import { useState } from 'react'
import { questionnaireApi, type AnalystReading, type BriefChecklist, type Decoding, type NoteLedger, type NoteRecord } from '../../api/client'
import InfoTip from '../ui/InfoTip'
import { askCopilot } from '../CopilotDrawer'

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
        {(brief.general_notes ?? 0) > 0 && <> · {brief.general_notes} study-wide scripting rules</>}
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

/**
 * What an analyst reading the scripter's notes would stop at: questions only a
 * person can settle (a click hands the choice to the Copilot, which prepares the
 * change as a card), and the variables the notes say to create.
 */
function AnalystSection({ analyst, studyId, onGoTo }: {
  analyst: AnalystReading
  studyId?: string
  onGoTo: (qid: string) => void
}) {
  const [creating, setCreating] = useState<string | null>(null)
  const [answered, setAnswered] = useState<Record<string, string>>({})
  const answer = (q: AnalystReading['questions'][number], option: string) => {
    if (q.key) {
      setAnswered((a) => ({ ...a, [q.key as string]: option }))
      if (studyId) void questionnaireApi.decideReading(studyId, q.key, q.ask, option)
    }
    askCopilot(`${q.ask}\nMy answer: ${option}`)
  }
  const ready = analyst.variables.filter((v) => v.status === 'ready')
  const createReady = async () => {
    if (!studyId) return
    setCreating('Creating…')
    let made = 0
    const failed: string[] = []
    for (const v of ready) {
      try {
        await questionnaireApi.addQuestion(studyId, {
          qid: v.name.toUpperCase().startsWith('HID_') ? v.name : `HID_${v.name}`,
          text: v.said || v.name,
          type: 'info',
          required: false,
          hidden: true,
          options: v.bands.map((b, i) => ({ code: b.code ?? i + 1, label: b.label, derive_if: b.condition })),
        })
        made += 1
      } catch {
        failed.push(v.name)
      }
    }
    setCreating(`Created ${made}${failed.length ? `; not created (already there?): ${failed.join(', ')}` : ''}.`)
    window.dispatchEvent(new CustomEvent('dvv:changed'))
  }
  if (!analyst.questions.length && !analyst.variables.length && !analyst.checks.length) return null
  return (
    <section>
      <h4 className="font-medium">What a reader would ask</h4>
      <p className="text-xs text-slate-500">
        Read from the scripter&apos;s notes. Pick an answer and the Copilot prepares the change for you to confirm.
      </p>
      {analyst.questions.length > 0 && (
        <ul className="mt-2 space-y-2">
          {analyst.questions.map((q, i) => (
            <li key={i} className="text-xs">
              <button className="font-mono underline mr-1 hover:text-slate-900" onClick={() => onGoTo(q.qid)}>
                {q.qid}
              </button>
              {q.ask}
              <div className="mt-1 flex flex-wrap gap-1">
                {q.options.map((o) => (
                  <button
                    key={o}
                    type="button"
                    onClick={() => answer(q, o)}
                    aria-pressed={(answered[q.key ?? ''] ?? q.decided?.answer) === o}
                    className={`rounded border px-2 py-0.5 hover:border-sky-300 hover:bg-sky-50 ${
                      (answered[q.key ?? ''] ?? q.decided?.answer) === o
                        ? 'border-sky-500 bg-sky-50 font-medium'
                        : 'border-slate-200 bg-white'
                    }`}
                  >
                    {o}
                  </button>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
      {analyst.variables.length > 0 && (
        <div className="mt-3 text-xs">
          <p>
            The notes ask for {analyst.variables.length} variables; {analyst.ready} have exact rules
            {analyst.ready > 0 && studyId ? ' and can be created now.' : '.'}
          </p>
          <ul className="mt-1 flex flex-wrap gap-1">
            {analyst.variables.map((v, i) => (
              <li key={i} title={v.why || v.bands.map((b) => b.line).join('\n')}
                className={`rounded px-1.5 py-0.5 ${v.status === 'ready' ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800'}`}>
                {v.name}
              </li>
            ))}
          </ul>
          {ready.length > 0 && studyId && (
            creating ? <p className="mt-1 text-emerald-800">{creating}</p> : (
              <button type="button" onClick={() => void createReady()}
                className="mt-2 rounded bg-emerald-600 px-2.5 py-1 text-white hover:bg-emerald-700">
                Create the {ready.length} ready variables
              </button>
            )
          )}
        </div>
      )}
      {analyst.checks.length > 0 && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-slate-600">
            {analyst.checks.length} checks for cleaning and scripting (quality flags, automatic answers)
          </summary>
          <ul className="mt-1 space-y-0.5 text-slate-600">
            {analyst.checks.map((c, i) => (
              <li key={i}><span className="font-mono">{c.qid}</span> · {c.said}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

type Outcome = NoteRecord['outcome']
// Best first: a sentence read two ways counts once, under its best outcome.
const OUTCOMES: { key: Outcome; label: string; done: string }[] = [
  { key: 'applied', label: 'Applied', done: 'Applied to the survey.' },
  { key: 'asked', label: 'Needs you', done: 'Waiting for your decision.' },
  { key: 'scripter', label: 'For the scripter', done: 'Cannot be set up here; it is on the scripter’s list.' },
  { key: 'by_design', label: 'Nothing to do', done: 'Says nothing new; nothing to change.' },
  { key: 'unread', label: 'Not understood', done: 'Nothing here understood this note.' },
]

/** One row per distinct (question, note), under its best outcome. */
export function noteRows(records: NoteRecord[]): NoteRecord[] {
  const rank = (o: Outcome) => OUTCOMES.findIndex((x) => x.key === o)
  const best = new Map<string, NoteRecord>()
  for (const r of records) {
    const k = JSON.stringify([r.qid, r.said])
    const cur = best.get(k)
    if (!cur || rank(r.outcome) < rank(cur.outcome)) best.set(k, r)
  }
  return [...best.values()]
}

/** Every note sentence and what became of it. Nothing is shown for projects read before the ledger existed. */
function NotesSection({ ledger, onGoTo }: { ledger: NoteLedger; onGoTo: (qid: string) => void }) {
  const [filter, setFilter] = useState<Outcome | null>(null)
  const rows = noteRows(ledger.records ?? [])
  if (!rows.length) return null
  const count = (o: Outcome) => rows.filter((r) => r.outcome === o).length
  const shown = filter ? rows.filter((r) => r.outcome === filter) : rows
  const parts = OUTCOMES.filter((o) => count(o.key) > 0)
    .map((o) => `${count(o.key)} ${o.key === 'applied' ? 'applied' : o.key === 'asked' ? 'need you' : o.key === 'scripter' ? 'for the scripter' : o.key === 'by_design' ? 'need nothing' : 'not understood'}`)
  return (
    <section>
      <h4 className="font-medium">Notes<InfoTip id="notes" /></h4>
      <p className="text-xs text-slate-500">{rows.length} notes: {parts.join(', ')}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        {OUTCOMES.filter((o) => count(o.key) > 0).map((o) => (
          <button key={o.key} type="button" aria-pressed={filter === o.key}
            onClick={() => setFilter(filter === o.key ? null : o.key)}
            className={`rounded border px-2 py-0.5 text-xs hover:border-sky-300 ${filter === o.key ? 'border-sky-500 bg-sky-50 font-medium' : 'border-slate-200 bg-white'}`}>
            {o.label} ({count(o.key)})
          </button>
        ))}
      </div>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-slate-600">
          {filter ? `Show the ${shown.length} notes` : `Show all ${shown.length} notes`}
        </summary>
        <ul className="mt-1 space-y-1.5" data-testid="note-rows">
          {shown.map((r, i) => (
            <li key={i}>
              <button className="font-mono underline mr-1 hover:text-slate-900" onClick={() => onGoTo(r.qid)}>{r.qid}</button>
              “{r.said}”
              <div className="text-slate-500">
                {OUTCOMES.find((o) => o.key === r.outcome)?.label}: {OUTCOMES.find((o) => o.key === r.outcome)?.done}
                {r.outcome === 'asked' && r.detail ? ` ${r.detail}` : ''}
              </div>
              {r.outcome === 'unread' && (
                <button type="button" className="mt-0.5 rounded border border-slate-200 bg-white px-2 py-0.5 hover:border-sky-300 hover:bg-sky-50"
                  onClick={() => askCopilot(`${r.qid} has this programming note that was not understood: “${r.said}”\nWhat does it mean, and how should it be set up?`)}>
                  Explain this to the Copilot
                </button>
              )}
            </li>
          ))}
        </ul>
      </details>
    </section>
  )
}

export default function DecodingPanel({
  decoding,
  onGoTo,
  studyId,
  noteLedger,
}: {
  decoding: Decoding | null | undefined
  noteLedger?: NoteLedger | null
  onGoTo: (qid: string) => void
  studyId?: string
}) {
  if (!decoding) return null
  const unfamiliar = decoding.verdict === 'unfamiliar'
  return (
    <details open={unfamiliar || (decoding.brief?.missing ?? 0) > 0 || (decoding.analyst?.questions.length ?? 0) > 0} className="bg-white border border-slate-200 rounded-md p-4 mb-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-800">
        How this questionnaire was read
        <span className={`ml-2 text-xs font-normal ${unfamiliar ? 'text-amber-700' : 'text-slate-500'}`}>
          {decoding.read_by_skills} of {decoding.questions} questions read by known skills
          {unfamiliar ? ' — unfamiliar format, check the list below' : ''}
        </span>
      </summary>
      <div className="mt-3 space-y-4 text-sm text-slate-700">
        {decoding.profile && (
          <p className="text-xs text-slate-600" data-testid="document-profile">
            <span className="font-medium">How this document is written:</span> {decoding.profile.layout}
            {Object.keys(decoding.profile.note_markers).length > 0 && (
              <> · notes marked {Object.keys(decoding.profile.note_markers).join(', ')}</>
            )}
            {' · '}routing written {decoding.profile.routing_position} each question
            {decoding.profile.answer_required_by_brief && <> · the brief requires an answer to every question</>}
          </p>
        )}
        {decoding.brief && <BriefSection brief={decoding.brief} onGoTo={onGoTo} />}
        {decoding.analyst && <AnalystSection analyst={decoding.analyst} studyId={studyId} onGoTo={onGoTo} />}
        {noteLedger && <NotesSection ledger={noteLedger} onGoTo={onGoTo} />}
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
