import { Fragment, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'

import {
  CopilotChart,
  CopilotConversationSummary,
  CopilotProposal,
  CopilotTable,
  copilotApi,
  errorMessage,
} from '../api/client'
import { downloadBlob, rowsToCSV } from '../lib/exportUtils'

/**
 * The copilot: ask anything about the platform; it prepares actions as cards.
 *
 * Nothing changes until a person presses Confirm, and Confirm sends only the
 * request the server built — with this user's own login, so the copilot can do
 * nothing its user couldn't. A successful action announces `dvv:changed` so an
 * open page (the field monitor) refreshes instead of showing stale numbers.
 */

type CardState = 'pending' | 'working' | 'done' | 'dismissed' | 'failed' | 'expired'

/**
 * Other pages talk to the copilot through two window events, so none of them
 * has to know where the drawer lives:
 *   dvv:copilot-open    {prefill?}       open it, optionally with a question typed
 *   dvv:question-focus  {studyId, qid}   the question open on the questionnaire page
 */
export function askCopilot(prefill?: string) {
  window.dispatchEvent(new CustomEvent('dvv:copilot-open', { detail: { prefill } }))
}
export function focusQuestion(studyId: string, qid: string | null) {
  window.dispatchEvent(new CustomEvent('dvv:question-focus', { detail: { studyId, qid } }))
}

/** The conversation to reopen, per browser. A convenience: losing it loses nothing. */
const LAST_KEY = 'dvv.copilot.conversation'
function remembered(): string | null {
  try {
    return localStorage.getItem(LAST_KEY)
  } catch {
    return null
  }
}
function remember(id: string | null) {
  try {
    if (id) localStorage.setItem(LAST_KEY, id)
    else localStorage.removeItem(LAST_KEY)
  } catch {
    /* private window: the conversation is still saved on the server */
  }
}

interface Turn {
  role: 'user' | 'assistant'
  content: string
  cards?: CopilotProposal[]
  tables?: CopilotTable[]
  charts?: CopilotChart[]
  answeredBy?: string
}

const fileName = (title: string, ext: string) =>
  `${(title || 'table').replace(/[^\w\- ]+/g, ' ').trim().slice(0, 60) || 'table'}.${ext}`

/** A chart image fetched with the user's login, with a PNG download. */
function ChartImage({ chart }: { chart: CopilotChart }) {
  const [blob, setBlob] = useState<Blob | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    let made: string | null = null
    copilotApi
      .chart(chart)
      .then((res) => {
        if (!live) return
        made = URL.createObjectURL(res.data)
        setBlob(res.data)
        setUrl(made)
      })
      .catch(() => live && setFailed(true))
    return () => {
      live = false
      if (made) URL.revokeObjectURL(made)
    }
  }, [chart])
  return (
    <figure className="mt-2 overflow-hidden rounded-md border border-slate-200" data-testid="copilot-chart">
      {failed && <p className="p-3 text-xs text-red-600">Couldn't load the chart.</p>}
      {!failed && !url && <p className="p-3 text-xs text-slate-500">Loading chart…</p>}
      {url && <img src={url} alt={chart.title} className="w-full" />}
      <figcaption className="flex items-center justify-between border-t border-slate-100 px-2 py-1 text-[11px] text-slate-500">
        <span className="truncate">{chart.title}</span>
        {blob && (
          <button type="button" className="font-medium text-primary-600 hover:underline"
            onClick={() => downloadBlob(fileName(chart.title, 'png'), blob, 'image/png')}>
            PNG
          </button>
        )}
      </figcaption>
    </figure>
  )
}

/** A workbook the copilot built ("every question by age band"), fetched with the user's login. */
function WorkbookDownload({ file }: { file: CopilotChart }) {
  const [state, setState] = useState<'idle' | 'busy' | 'failed'>('idle')
  const save = async () => {
    setState('busy')
    try {
      const res = await copilotApi.chart(file)
      downloadBlob(file.filename, res.data,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
      setState('idle')
    } catch {
      setState('failed')
    }
  }
  return (
    <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-slate-200 px-2 py-1.5 text-[11px]"
      data-testid="copilot-workbook">
      <span className="truncate text-slate-600">{file.title}</span>
      <button type="button" disabled={state === 'busy'} onClick={save}
        className="shrink-0 font-medium text-primary-600 hover:underline disabled:opacity-50">
        {state === 'busy' ? 'Downloading…' : state === 'failed' ? 'Retry download' : 'Download Excel'}
      </button>
    </div>
  )
}

/** Downloads for one answer's tables: each as CSV, all together as one Excel workbook. */
function TableDownloads({ tables }: { tables: CopilotTable[] }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const excel = async () => {
    setBusy(true)
    setFailed(false)
    try {
      const res = await copilotApi.excel(tables)
      downloadBlob(fileName(tables.length === 1 ? tables[0].title : 'copilot tables', 'xlsx'), res.data,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
      <button type="button" onClick={excel} disabled={busy}
        className="font-medium text-primary-600 hover:underline disabled:opacity-50">
        {busy ? 'Preparing…' : tables.length > 1 ? `Excel (${tables.length} tables)` : 'Excel'}
      </button>
      {tables.map((t, i) => (
        <button key={i} type="button" className="font-medium text-primary-600 hover:underline"
          onClick={() => downloadBlob(fileName(t.title, 'csv'), rowsToCSV(t.header, t.rows), 'text/csv')}>
          CSV{tables.length > 1 ? ` ${i + 1}` : ''}
        </button>
      ))}
      {failed && <span className="text-red-600">Excel download failed.</span>}
    </div>
  )
}

/** The project in view, from the URL — context for the copilot, not a permission. */
export function studyFromPath(pathname: string): string | null {
  const m = pathname.match(/^\/projects\/([^/]+)/)
  return m && m[1] !== 'new' ? decodeURIComponent(m[1]) : null
}

/** Bold (**x**) and "- " bullets rendered as elements, never as raw HTML. */
/** A markdown pipe-table, as rows of cells. Null when these lines are not one. */
function parseTable(lines: string[]): string[][] | null {
  const cells = (line: string) =>
    line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
  if (lines.length < 2) return null
  const header = cells(lines[0])
  // The second line of a markdown table is the ---|--- rule; without it this is
  // prose that happens to contain a pipe.
  if (!/^[\s|:-]+$/.test(lines[1]) || cells(lines[1]).length !== header.length) return null
  const body = lines.slice(2).filter((l) => l.includes('|')).map(cells)
  return body.length ? [header, ...body] : null
}

const asNumber = (cell: string) => {
  const n = Number(String(cell).replace(/[%,\s]/g, ''))
  return Number.isFinite(n) ? n : null
}

/**
 * A table, and a bar for each row when the table is one label and one number.
 *
 * A crossbreak IS the answer — a sentence about its direction is not — and in a
 * narrow panel a row of bars is read faster than the digits beside them. No
 * charting library: a div with a width is enough and cannot break the panel.
 */
function DataTable({ rows }: { rows: string[][] }) {
  const [header, ...body] = rows
  const numeric = header.map((_, col) =>
    body.every((r) => r[col] !== undefined && asNumber(r[col]) !== null),
  )
  // Which column the bars are about. A percentage if the table has one — it is
  // what a crossbreak is read on, and a base or an unweighted n beside it is
  // context rather than the comparison. Otherwise the only numeric column, and
  // if there are several with no percentage among them, no bars: guessing which
  // of three numbers matters would draw a chart of the wrong thing.
  const percentCol = header.findIndex((h, i) => numeric[i] && /%|pct|percent/i.test(h))
  const chartCol =
    percentCol >= 0 ? percentCol : numeric.filter(Boolean).length === 1 ? numeric.indexOf(true) : -1
  const peak =
    chartCol >= 0 ? Math.max(...body.map((r) => Math.abs(asNumber(r[chartCol]) ?? 0)), 1) : 1

  return (
    <div className="my-2 overflow-x-auto rounded-md border border-slate-200">
      <table className="w-full text-xs">
        <thead className="bg-slate-50 text-left text-slate-500">
          <tr>
            {header.map((cell, i) => (
              <th key={i} className={`px-2 py-1.5 font-medium ${numeric[i] ? 'text-right' : ''}`}>
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {body.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => (
                <td key={c} className={`px-2 py-1.5 ${numeric[c] ? 'text-right tabular-nums' : ''}`}>
                  {c === chartCol ? (
                    <span className="flex items-center justify-end gap-1.5">
                      <span
                        aria-hidden
                        className="h-1.5 rounded-sm bg-primary-400"
                        style={{
                          width: `${Math.round(
                            (Math.abs(asNumber(cell) ?? 0) / peak) * 56,
                          )}px`,
                        }}
                      />
                      {cell}
                    </span>
                  ) : (
                    cell
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Formatted({ text }: { text: string }) {
  const inline = (line: string) =>
    line.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith('**') && part.endsWith('**') ? (
        <strong key={i}>{part.slice(2, -2)}</strong>
      ) : (
        <Fragment key={i}>{part}</Fragment>
      ),
    )
  // Pipe-table blocks come out as a real table; everything else as before. A
  // crosstab printed as "| Age band | Aware n |" was reaching the panel as raw
  // pipes, which is the shape it arrives in and not one anybody can read.
  const blocks: Array<{ table: string[][] } | { text: string[] }> = []
  let run: string[] = []
  const flushText = () => {
    if (run.length) blocks.push({ text: run })
    run = []
  }
  const lines = text.split('\n')
  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i].includes('|')) {
      let end = i
      while (end + 1 < lines.length && lines[end + 1].includes('|')) end += 1
      const table = parseTable(lines.slice(i, end + 1))
      if (table) {
        flushText()
        blocks.push({ table })
        i = end
        continue
      }
    }
    run.push(lines[i])
  }
  flushText()

  return (
    <div className="space-y-1">
      {blocks.map((block, b) =>
        'table' in block ? (
          <DataTable key={b} rows={block.table} />
        ) : (
          <Fragment key={b}>
            {block.text.map((line, i) => {
              const bullet = line.match(/^\s*[-•*]\s+(.*)/)
              if (bullet) return <div key={i} className="pl-3 -indent-3">• {inline(bullet[1])}</div>
              return line.trim() ? <p key={i}>{inline(line)}</p> : <div key={i} className="h-1" />
            })}
          </Fragment>
        ),
      )}
    </div>
  )
}

function Card({
  card,
  state,
  error,
  onConfirm,
  onDismiss,
}: {
  card: CopilotProposal
  state: CardState
  error?: string
  onConfirm: () => void
  onDismiss: () => void
}) {
  return (
    <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
      <p className="font-medium text-slate-900">{card.title}</p>
      {card.diff && card.diff.length > 0 && (
        <table className="mt-2 w-full text-xs">
          <tbody>
            {card.diff.map((d) => (
              <tr key={d.field} className="align-top border-t border-amber-200/70">
                <td className="py-1 pr-2 font-medium text-slate-500">{d.field}</td>
                <td className="py-1 pr-2 text-slate-500 line-through decoration-slate-300">{d.before}</td>
                <td className="py-1 text-slate-900">{d.after}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="text-slate-600 mt-1">{card.detail}</p>
      {state === 'pending' && (
        <div className="flex gap-2 mt-2.5">
          <button
            type="button"
            onClick={onConfirm}
            className="text-xs bg-primary-500 text-white font-medium px-3 py-1.5 rounded-md hover:bg-primary-600"
          >
            Confirm
          </button>
          <button
            type="button"
            onClick={onDismiss}
            className="text-xs border border-slate-300 text-slate-700 font-medium px-3 py-1.5 rounded-md hover:bg-white"
          >
            Dismiss
          </button>
        </div>
      )}
      {state === 'working' && <p className="text-xs text-slate-500 mt-2">Working…</p>}
      {state === 'done' && <p className="text-xs text-emerald-700 mt-2 font-medium">Done.</p>}
      {state === 'dismissed' && <p className="text-xs text-slate-400 mt-2">Dismissed — nothing changed.</p>}
      {state === 'failed' && <p className="text-xs text-red-600 mt-2">{error || 'That did not work.'}</p>}
      {state === 'expired' && (
        <p className="text-xs text-slate-500 mt-2">
          From an earlier session, so it can no longer be confirmed — the project may have changed
          since. Ask again for a fresh card.
        </p>
      )}
    </div>
  )
}

export default function CopilotDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { pathname } = useLocation()
  const studyId = studyFromPath(pathname)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cardState, setCardState] = useState<Record<string, CardState>>({})
  const [cardError, setCardError] = useState<Record<string, string>>({})
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [earlier, setEarlier] = useState<CopilotConversationSummary[]>([])
  const [focus, setFocus] = useState<{ studyId: string; qid: string | null } | null>(null)
  // Only the question open in THIS project counts.
  const focusQid = focus && focus.studyId === studyId ? focus.qid : null

  useEffect(() => {
    const onFocus = (e: Event) => setFocus((e as CustomEvent).detail)
    const onOpen = (e: Event) => {
      const prefill = (e as CustomEvent).detail?.prefill
      if (prefill) setInput(prefill)
    }
    window.addEventListener('dvv:question-focus', onFocus)
    window.addEventListener('dvv:copilot-open', onOpen)
    return () => {
      window.removeEventListener('dvv:question-focus', onFocus)
      window.removeEventListener('dvv:copilot-open', onOpen)
    }
  }, [])
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [turns, busy])

  const load = async (id: string) => {
    try {
      const res = await copilotApi.conversation(id)
      const expired: Record<string, CardState> = {}
      setTurns(
        res.data.messages.map((m) => {
          for (const c of m.proposals) expired[c.id] = 'expired'
          return {
            role: m.role,
            content: m.content,
            cards: m.proposals,
            tables: m.tables,
            charts: m.charts,
            answeredBy: m.answered_by ?? undefined,
          }
        }),
      )
      setCardState(expired)
      setCardError({})
      setConversationId(id)
      remember(id)
    } catch {
      remember(null) // deleted, or not this user's: start fresh
    }
  }

  // Opening the panel picks up where this browser left off.
  useEffect(() => {
    if (!open) return
    const last = remembered()
    if (last && !conversationId) void load(last)
    copilotApi
      .conversations()
      .then((res) => setEarlier(res.data))
      .catch(() => setEarlier([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const startNew = () => {
    setTurns([])
    setCardState({})
    setCardError({})
    setError(null)
    setConversationId(null)
    remember(null)
  }

  if (!open) return null

  const send = async (e: React.FormEvent) => {
    e.preventDefault()
    const message = input.trim()
    if (!message || busy) return
    setTurns((t) => [...t, { role: 'user', content: message }])
    setInput('')
    setBusy(true)
    setError(null)
    try {
      const res = await copilotApi.ask(message, studyId, conversationId, focusQid)
      if (res.data.conversation_id && res.data.conversation_id !== conversationId) {
        setConversationId(res.data.conversation_id)
        remember(res.data.conversation_id)
      }
      setTurns((t) => [
        ...t,
        {
          role: 'assistant',
          content: res.data.reply,
          cards: res.data.proposals,
          tables: res.data.tables,
          charts: res.data.charts,
          answeredBy: res.data.answered_by,
        },
      ])
    } catch (err) {
      setError(errorMessage(err) || 'The copilot could not answer.')
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (card: CopilotProposal) => {
    setCardState((s) => ({ ...s, [card.id]: 'working' }))
    try {
      await copilotApi.confirm(card)
      setCardState((s) => ({ ...s, [card.id]: 'done' }))
      window.dispatchEvent(new CustomEvent('dvv:changed'))
    } catch (err) {
      setCardState((s) => ({ ...s, [card.id]: 'failed' }))
      setCardError((s) => ({ ...s, [card.id]: errorMessage(err) || '' }))
    }
  }

  return (
    <aside
      aria-label="Copilot"
      className="fixed right-0 top-14 bottom-0 z-40 w-full max-w-[420px] bg-white border-l border-slate-200 shadow-xl flex flex-col"
    >
      <div className="flex items-start justify-between px-4 py-3 border-b border-slate-100">
        <div>
          <p className="font-semibold text-slate-900">Copilot</p>
          <p className="text-xs text-slate-500">
            {studyId ? `Looking at ${studyId}${focusQid ? ` · ${focusQid}` : ''}. ` : ''}Open-source models. It prepares actions;
            nothing changes until you confirm.
          </p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {earlier.length > 0 && (
            <select
              aria-label="Earlier conversations"
              value=""
              onChange={(e) => e.target.value && void load(e.target.value)}
              className="h-7 max-w-[7.5rem] rounded border border-slate-200 px-1 text-xs text-slate-600"
            >
              <option value="">Earlier…</option>
              {earlier.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            onClick={startNew}
            disabled={busy || turns.length === 0}
            className="h-7 rounded border border-slate-200 px-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >
            New chat
          </button>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close copilot"
          className="text-slate-400 hover:text-slate-700 text-lg leading-none px-1"
        >
          ×
        </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 text-sm">
        {turns.length === 0 && (
          <div className="text-slate-500 space-y-1.5">
            <p>Try:</p>
            {[
              'What projects do we have, and where does each stand?',
              'Why are people being screened out, and what is the incidence?',
              'Run the soft-launch check on this project.',
            ].map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setInput(s)}
                className="block text-left text-primary-600 hover:underline"
              >
                {s}
              </button>
            ))}
          </div>
        )}
        {turns.map((turn, i) => (
          <div key={i} className={turn.role === 'user' ? 'flex justify-end' : ''}>
            <div
              className={
                turn.role === 'user'
                  ? 'max-w-[85%] rounded-lg bg-primary-500 text-white px-3 py-2 whitespace-pre-wrap'
                  : 'text-slate-800'
              }
            >
              {turn.role === 'user' ? turn.content : <Formatted text={turn.content} />}
              {turn.cards?.map((card) => (
                <Card
                  key={card.id}
                  card={card}
                  state={cardState[card.id] ?? 'pending'}
                  error={cardError[card.id]}
                  onConfirm={() => confirm(card)}
                  onDismiss={() => setCardState((s) => ({ ...s, [card.id]: 'dismissed' }))}
                />
              ))}
              {turn.tables?.map((t, ti) => (
                <div key={ti} className="mt-2" data-testid="copilot-table">
                  <DataTable rows={[t.header, ...t.rows]} />
                  <p className="mt-0.5 text-[10px] text-slate-400">
                    As computed by the tables engine
                    {t.significance_tested ? '; letters mark significant differences' : '; no significance test'}
                    {t.truncated ? `; first ${t.rows.length} rows` : ''}.
                  </p>
                </div>
              ))}
              {turn.tables && turn.tables.length > 0 && <TableDownloads tables={turn.tables} />}
              {turn.charts?.map((c) => c.kind === 'workbook'
                ? <WorkbookDownload key={c.filename} file={c} />
                : <ChartImage key={c.filename} chart={c} />)}
              {turn.answeredBy && (
                <p className="text-[10px] text-slate-400 mt-1">answered by {turn.answeredBy}</p>
              )}
            </div>
          </div>
        ))}
        {busy && <p className="text-slate-400">Looking into it…</p>}
        {error && <p className="text-red-600">{error}</p>}
        <div ref={endRef} />
      </div>

      <form onSubmit={send} className="border-t border-slate-100 p-3 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about any project…"
          maxLength={2000}
          className="flex-1 h-9 px-3 rounded-md border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="h-9 px-3 bg-primary-500 text-white text-sm font-medium rounded-md hover:bg-primary-600 disabled:opacity-50"
        >
          Ask
        </button>
      </form>
    </aside>
  )
}
