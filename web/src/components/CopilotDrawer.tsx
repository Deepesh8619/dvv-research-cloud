import { Fragment, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'

import { CopilotProposal, copilotApi, errorMessage } from '../api/client'

/**
 * The copilot: ask anything about the platform; it prepares actions as cards.
 *
 * Nothing changes until a person presses Confirm, and Confirm sends only the
 * request the server built — with this user's own login, so the copilot can do
 * nothing its user couldn't. A successful action announces `dvv:changed` so an
 * open page (the field monitor) refreshes instead of showing stale numbers.
 */

type CardState = 'pending' | 'working' | 'done' | 'dismissed' | 'failed'

interface Turn {
  role: 'user' | 'assistant'
  content: string
  cards?: CopilotProposal[]
  answeredBy?: string
}

/** The project in view, from the URL — context for the copilot, not a permission. */
export function studyFromPath(pathname: string): string | null {
  const m = pathname.match(/^\/projects\/([^/]+)/)
  return m && m[1] !== 'new' ? decodeURIComponent(m[1]) : null
}

/** Bold (**x**) and "- " bullets rendered as elements, never as raw HTML. */
function Formatted({ text }: { text: string }) {
  const inline = (line: string) =>
    line.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith('**') && part.endsWith('**') ? (
        <strong key={i}>{part.slice(2, -2)}</strong>
      ) : (
        <Fragment key={i}>{part}</Fragment>
      ),
    )
  return (
    <div className="space-y-1">
      {text.split('\n').map((line, i) => {
        const bullet = line.match(/^\s*[-•*]\s+(.*)/)
        if (bullet) return <div key={i} className="pl-3 -indent-3">• {inline(bullet[1])}</div>
        return line.trim() ? <p key={i}>{inline(line)}</p> : <div key={i} className="h-1" />
      })}
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
      <p className="text-slate-600 mt-0.5">{card.detail}</p>
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
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' })
  }, [turns, busy])

  if (!open) return null

  const send = async (e: React.FormEvent) => {
    e.preventDefault()
    const message = input.trim()
    if (!message || busy) return
    const history = turns.map(({ role, content }) => ({ role, content: content.slice(0, 3900) }))
    setTurns((t) => [...t, { role: 'user', content: message }])
    setInput('')
    setBusy(true)
    setError(null)
    try {
      const res = await copilotApi.ask(message, history, studyId)
      setTurns((t) => [
        ...t,
        {
          role: 'assistant',
          content: res.data.reply,
          cards: res.data.proposals,
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
            {studyId ? `Looking at ${studyId}. ` : ''}Open-source models. It prepares actions;
            nothing changes until you confirm.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close copilot"
          className="text-slate-400 hover:text-slate-700 text-lg leading-none px-1"
        >
          ×
        </button>
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
