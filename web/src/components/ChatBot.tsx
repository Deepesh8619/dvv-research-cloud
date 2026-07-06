import { useCallback, useEffect, useRef, useState } from 'react'
import api, { chatApi, type ChatMessage } from '../api/client'
import { downloadBlob, toCSV, toMarkdownTable } from '../lib/exportUtils'
import { ChatIcon } from './Icons'

interface Props {
  studyId: string
  clientName: string
  onNavigate?: (tab: string, tableId?: string) => void
}

interface ToolResult {
  tool: string
  success: boolean
  [key: string]: unknown
}

interface MessageWithTools extends ChatMessage {
  tool_results?: ToolResult[]
}

export default function ChatBot({ studyId, clientName, onNavigate }: Props) {
  const [isOpen, setIsOpen] = useState(false)
  const [messages, setMessages] = useState<MessageWithTools[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [hasLoadedHistory, setHasLoadedHistory] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Load persisted history from localStorage (per study)
  useEffect(() => {
    const key = `chat_history_${studyId}`
    const stored = localStorage.getItem(key)
    if (stored) {
      try {
        const parsed = JSON.parse(stored) as MessageWithTools[]
        setMessages(parsed)
      } catch {
        // ignore corrupted storage
      }
    }
    setHasLoadedHistory(true)
  }, [studyId])

  // Persist history when messages change
  useEffect(() => {
    if (!hasLoadedHistory) return
    const key = `chat_history_${studyId}`
    localStorage.setItem(key, JSON.stringify(messages))
  }, [messages, hasLoadedHistory, studyId])

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // Focus input when opening
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [isOpen])

  // Load server history on first open (merges with localStorage)
  useEffect(() => {
    if (!isOpen || hasLoadedHistory) return
    chatApi
      .history(studyId, 50)
      .then((res) => {
        const serverMessages = res.data.messages.map((m) => ({
          ...m,
          tool_results: undefined as ToolResult[] | undefined,
        }))
        setMessages(serverMessages)
      })
      .catch(() => {
        // Server history is optional; localStorage is the source of truth
      })
  }, [isOpen, studyId, hasLoadedHistory])

  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || loading) return
      const userMsg: MessageWithTools = {
        id: `local_${Date.now()}`,
        role: 'user',
        content: text.trim(),
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, userMsg])
      setInput('')
      setLoading(true)

      try {
        const res = await chatApi.send(studyId, text.trim())
        const assistantMsg: MessageWithTools = {
          ...res.data.message,
          tool_results: res.data.tool_results as ToolResult[] | undefined,
        }
        setMessages((prev) => [...prev, assistantMsg])
      } catch (err) {
        const errorMsg: MessageWithTools = {
          id: `local_err_${Date.now()}`,
          role: 'assistant',
          content: 'Sorry, I had trouble processing that request. Please try again.',
          created_at: new Date().toISOString(),
        }
        setMessages((prev) => [...prev, errorMsg])
      } finally {
        setLoading(false)
      }
    },
    [loading, studyId],
  )

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    sendMessage(input)
  }

  const handleSuggestedClick = (question: string) => {
    sendMessage(question)
  }

  return (
    <>
      {/* Floating chat button */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="fixed bottom-6 right-6 z-50 w-14 h-14 rounded-full bg-primary-500 text-white shadow-lg hover:bg-primary-600 flex items-center justify-center transition-transform hover:scale-105"
          title="Open AI assistant"
        >
          <ChatIcon className="w-6 h-6" />
        </button>
      )}

      {/* Chat panel */}
      {isOpen && (
        <div className="fixed bottom-6 right-6 z-50 w-[420px] h-[560px] bg-white rounded-xl shadow-2xl border border-slate-200 flex flex-col overflow-hidden">
          {/* Header */}
          <div className="bg-primary-500 text-white px-4 py-3 flex items-center justify-between shrink-0">
            <div>
              <h3 className="font-semibold text-sm">AI Assistant</h3>
              <p className="text-[10px] text-white/70 truncate max-w-[280px]">{clientName}</p>
            </div>
            <div className="flex items-center gap-2">
              {messages.length > 0 && (
                <button
                  onClick={() => {
                    setMessages([])
                    localStorage.removeItem(`chat_history_${studyId}`)
                  }}
                  className="text-[10px] text-white/70 hover:text-white px-2 py-1 rounded hover:bg-white/10"
                  title="Clear chat"
                >
                  Clear
                </button>
              )}
              <button
                onClick={() => setIsOpen(false)}
                className="text-white/70 hover:text-white p-1"
                title="Close"
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>

          {/* Messages area */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.length === 0 && (
              <div className="space-y-3">
                <p className="text-sm text-slate-500 text-center py-4">
                  Ask me anything about this study. I can show tables, run SQL queries, and compare results.
                </p>
                <div className="space-y-2">
                  {SUGGESTED_QUESTIONS.map((q) => (
                    <button
                      key={q}
                      onClick={() => handleSuggestedClick(q)}
                      className="w-full text-left px-3 py-2 text-sm text-slate-600 bg-slate-50 rounded-md hover:bg-slate-100 border border-slate-100 transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <MessageBubble
                key={msg.id || i}
                message={msg}
                studyId={studyId}
                onNavigate={onNavigate}
              />
            ))}

            {loading && (
              <div className="flex items-start gap-2">
                <div className="w-7 h-7 rounded-full bg-primary-500 flex items-center justify-center shrink-0">
                  <ChatIcon className="w-4 h-4 text-white" />
                </div>
                <div className="bg-slate-100 rounded-lg px-3 py-2 text-sm text-slate-600">
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" />
                    <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }} />
                    <span className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }} />
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <form onSubmit={handleSubmit} className="border-t border-slate-200 p-3 flex gap-2 shrink-0">
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about this study..."
              className="flex-1 h-9 px-3 rounded-md bg-white border border-slate-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary-400"
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="h-9 px-3 bg-primary-500 text-white text-sm font-medium rounded-md hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" strokeLinejoin="round" />
              </svg>
              Send
            </button>
          </form>
        </div>
      )}
    </>
  )
}

const SUGGESTED_QUESTIONS = [
  'What are the top-level findings?',
  'Show me the purchase intent table',
  'Which tables have significant differences?',
  'Run SQL: What is the average sample size per column?',
]

function MessageBubble({
  message,
  studyId,
  onNavigate,
}: {
  message: MessageWithTools
  studyId: string
  onNavigate?: (tab: string, tableId?: string) => void
}) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex items-start gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-primary-500 flex items-center justify-center shrink-0">
          <ChatIcon className="w-4 h-4 text-white" />
        </div>
      )}
      <div
        className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
          isUser ? 'bg-primary-500 text-white' : 'bg-slate-100 text-slate-800'
        }`}
      >
        <div className="whitespace-pre-wrap">{message.content}</div>
        {!isUser && message.content && (
          // The LLM writes code/formulas/analysis directly into this text
          // (generate_code returns schema only, never code — see that case
          // below), so this is the only copy affordance for anything the
          // model authored in prose rather than returning as a tool result.
          <MessageCopyButton text={message.content} />
        )}
        {!isUser && message.tool_results && message.tool_results.length > 0 && (
          <div className="mt-2 space-y-2">
            {message.tool_results.map((result, i) => (
              <ToolResultRenderer key={i} result={result} studyId={studyId} onNavigate={onNavigate} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/** Tiny copy-to-clipboard link under an assistant message's own text. */
function MessageCopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 1200)
      }}
      className="mt-1 text-[10px] text-slate-400 hover:text-slate-700 transition-colors"
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  )
}

// Small inline Copy/Export toolbar, reused under every renderable tool result.
// copyLabel/exportLabel let callers say "Copy" vs "Copy image URL" etc. without
// duplicating the button markup for every tool-result case.
function ResultToolbar({
  onCopy,
  onExport,
  copyLabel = 'Copy',
  exportLabel = 'Export',
}: {
  onCopy?: () => void
  onExport?: () => void
  copyLabel?: string
  exportLabel?: string
}) {
  const [copied, setCopied] = useState(false)
  if (!onCopy && !onExport) return null

  const handleCopy = () => {
    onCopy?.()
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }

  return (
    <div className="flex items-center gap-1.5 px-2 py-1 border-t border-slate-100 bg-slate-50">
      {onCopy && (
        <button
          onClick={handleCopy}
          className="text-[10px] text-slate-500 hover:text-slate-800 px-1.5 py-0.5 rounded hover:bg-slate-200 transition-colors"
        >
          {copied ? 'Copied!' : copyLabel}
        </button>
      )}
      {onExport && (
        <button
          onClick={onExport}
          className="text-[10px] text-slate-500 hover:text-slate-800 px-1.5 py-0.5 rounded hover:bg-slate-200 transition-colors"
        >
          {exportLabel}
        </button>
      )}
    </div>
  )
}

// Fetches the chart PNG through the authenticated axios instance (a plain
// <img src="/api/..."> tag can't attach the bearer token, so it 401s) and
// renders it as a blob object URL. Same client the rest of the app uses for
// every API call (see web/src/api/client.ts).
function ChartResult({ path, filename, title }: { path: string; filename: string; title: string }) {
  const [blob, setBlob] = useState<Blob | null>(null)
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    let url: string | null = null
    api
      .get(path, { responseType: 'blob' })
      .then((res) => {
        if (!active) return
        const b = res.data as Blob
        url = URL.createObjectURL(b)
        setBlob(b)
        setBlobUrl(url)
      })
      .catch(() => active && setFailed(true))
    return () => {
      active = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [path])

  return (
    <div className="bg-white rounded border border-slate-200 overflow-hidden text-xs">
      <div className="bg-primary-50 px-2 py-1.5 text-xs font-medium text-primary-700">Chart: {title}</div>
      {failed && <div className="p-3 text-red-600">Couldn't load the chart image.</div>}
      {!failed && !blobUrl && <div className="p-3 text-slate-500">Loading chart…</div>}
      {blobUrl && <img src={blobUrl} alt={title} className="w-full" />}
      <ResultToolbar
        onExport={blob ? () => downloadBlob(filename, blob, 'image/png') : undefined}
        exportLabel="Download PNG"
      />
    </div>
  )
}

function ToolResultRenderer({
  result,
  studyId,
  onNavigate,
}: {
  result: ToolResult
  studyId: string
  onNavigate?: (tab: string, tableId?: string) => void
}) {
  if (!result.success) {
    return (
      <div className="text-xs bg-red-50 text-red-700 rounded px-2 py-1 border border-red-100">
        Tool error: {String(result.error || 'Unknown error')}
      </div>
    )
  }

  switch (result.tool) {
    case 'query_data': {
      const cols = result.columns as string[]
      const rows = (result.rows as Record<string, unknown>[]) || []
      if (!cols || cols.length === 0) return null
      return (
        <div className="bg-white rounded border border-slate-200 overflow-hidden text-xs">
          <div className="bg-slate-50 px-2 py-1 text-[10px] text-slate-500 font-medium uppercase tracking-wide">
            SQL Result ({String(result.row_count)} rows{result.truncated ? ', truncated' : ''})
          </div>
          <div className="overflow-x-auto max-h-[200px]">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-100">
                  {cols.map((c) => (
                    <th key={c} className="px-2 py-1 text-left font-medium text-slate-600 whitespace-nowrap">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="border-b border-slate-50 hover:bg-slate-50">
                    {cols.map((c) => (
                      <td key={c} className="px-2 py-1 text-slate-700 whitespace-nowrap tabular-nums">
                        {String(row[c] ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ResultToolbar
            onCopy={() =>
              navigator.clipboard.writeText(
                toMarkdownTable(cols, rows.map((r) => cols.map((c) => String(r[c] ?? '')))),
              )
            }
            onExport={() => downloadBlob('query_result.csv', toCSV(cols, rows), 'text/csv')}
          />
        </div>
      )
    }

    case 'show_table': {
      const table = result.table as Record<string, unknown> | undefined
      if (!table) return null
      const columns = (table.columns as Array<{ label: string; letter: string | null; base: number }>) || []
      const rows = (table.rows as Array<{ label: string; pct: number[]; sig: string[]; kind: string }>) || []
      const means = table.means as number[] | null
      const csvColumns = ['', ...columns.map((c) => c.label)]
      const csvRows: Record<string, unknown>[] = rows.map((row) => {
        const entry: Record<string, unknown> = { '': row.label }
        columns.forEach((c, j) => {
          entry[c.label] = row.pct[j]?.toFixed(1) ?? ''
        })
        return entry
      })
      if (means) {
        const meanRow: Record<string, unknown> = { '': 'Mean' }
        columns.forEach((c, j) => {
          meanRow[c.label] = means[j]?.toFixed(2) ?? ''
        })
        csvRows.push(meanRow)
      }
      return (
        <div className="bg-white rounded border border-slate-200 overflow-hidden text-xs">
          <div className="bg-primary-50 px-2 py-1.5 text-xs font-medium text-primary-700">
            Table: {String(table.title || table.stub)}
          </div>
          <div className="overflow-x-auto max-h-[200px] p-2">
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr>
                  <th className="text-left py-1 pr-3 font-medium text-slate-500">Column %</th>
                  {columns.slice(0, 6).map((c, j) => (
                    <th key={j} className="text-center py-1 px-2 font-medium text-slate-600 min-w-[60px]">
                      {c.label}
                      {c.letter && <span className="text-[10px] text-slate-400 ml-0.5">({c.letter})</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 8).map((row, i) => (
                  <tr key={i} className={`border-t border-slate-100 ${row.kind === 'net' ? 'font-semibold bg-slate-50' : ''}`}>
                    <td className="py-1 pr-3 text-slate-700">{row.label}</td>
                    {row.pct.slice(0, 6).map((pct, j) => (
                      <td key={j} className="text-center py-1 px-2 tabular-nums">
                        {pct.toFixed(1)}
                        {row.sig[j] && (
                          <sup className="text-emerald-600 font-bold ml-0.5">{row.sig[j]}</sup>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
                {means && (
                  <tr className="border-t border-slate-300 font-medium italic">
                    <td className="py-1 pr-3">Mean</td>
                    {means.slice(0, 6).map((m, j) => (
                      <td key={j} className="text-center py-1 tabular-nums">{m.toFixed(2)}</td>
                    ))}
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <ResultToolbar
            onCopy={() =>
              navigator.clipboard.writeText(
                toMarkdownTable(csvColumns, csvRows.map((r) => csvColumns.map((c) => String(r[c] ?? '')))),
              )
            }
            onExport={() => downloadBlob(`${String(table.stub || 'table')}.csv`, toCSV(csvColumns, csvRows), 'text/csv')}
          />
        </div>
      )
    }

    case 'compare_tables': {
      const tables = (result.tables as Array<Record<string, unknown>>) || []
      return (
        <div className="bg-white rounded border border-slate-200 overflow-hidden text-xs">
          <div className="bg-slate-50 px-2 py-1 text-[10px] text-slate-500 font-medium uppercase tracking-wide">
            Comparison ({tables.length} tables)
          </div>
          <div className="p-2 space-y-2">
            {tables.map((t, i) => (
              <div key={i} className="border border-slate-100 rounded p-2">
                <div className="font-medium text-slate-700 mb-1">{String(t.title || t.stub)}</div>
                <div className="text-slate-500 text-[10px]">
                  {(t.columns as Array<{ label: string }>)?.slice(0, 4).map((c) => c.label).join(', ')}
                </div>
              </div>
            ))}
          </div>
        </div>
      )
    }

    case 'generate_chart': {
      const chartFilename = result.chart_filename as string | undefined
      const title = String(result.title || 'chart')
      if (!chartFilename) return null
      // Chart PNGs are served from the study-scoped chart route
      // (api/routes/studies.py get_chart), which requires the same bearer
      // auth as every other API call — studyId comes from the ChatBot prop
      // (this panel is always scoped to one study), not the tool result.
      const path = `/studies/${studyId}/charts/${chartFilename}`
      return <ChartResult path={path} filename={chartFilename} title={title} />
    }

    case 'generate_pivot': {
      const cols = (result.columns as string[]) || []
      const rows = (result.rows as Record<string, unknown>[]) || []
      const rowsVar = String(result.rows_variable || 'row')
      const allCols = [rowsVar, ...cols]
      if (cols.length === 0) return null
      return (
        <div className="bg-white rounded border border-slate-200 overflow-hidden text-xs">
          <div className="bg-slate-50 px-2 py-1 text-[10px] text-slate-500 font-medium uppercase tracking-wide">
            Pivot ({String(result.aggfunc || 'count')})
          </div>
          <div className="overflow-x-auto max-h-[200px]">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-100">
                  {allCols.map((c) => (
                    <th key={c} className="px-2 py-1 text-left font-medium text-slate-600 whitespace-nowrap">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="border-b border-slate-50 hover:bg-slate-50">
                    <td className="px-2 py-1 text-slate-700 whitespace-nowrap font-medium">
                      {String(row.row ?? '')}
                    </td>
                    {cols.map((c) => (
                      <td key={c} className="px-2 py-1 text-slate-700 whitespace-nowrap tabular-nums">
                        {String(row[c] ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ResultToolbar
            onCopy={() =>
              navigator.clipboard.writeText(
                toMarkdownTable(
                  allCols,
                  rows.map((r) => [String(r.row ?? ''), ...cols.map((c) => String(r[c] ?? ''))]),
                ),
              )
            }
            onExport={() => {
              const csvRows = rows.map((r) => ({ [rowsVar]: r.row, ...r }))
              downloadBlob('pivot.csv', toCSV(allCols, csvRows), 'text/csv')
            }}
          />
        </div>
      )
    }

    case 'generate_code': {
      // generate_code returns schema only (see src/engines/analysis/codegen.py) —
      // the LLM writes the actual SQL/Python/formula in its reply text, not here.
      // executed_result (present when the model already ran a SQL draft via the
      // sandbox) is the closest thing to "generated code output" this tool
      // result carries, so that's what Copy/Export operate on.
      const language = String(result.language || 'text')
      const schema = (result.schema as Array<{ name: string; type: string; label: string }>) || []
      const executed = result.executed_result as
        | { success?: boolean; columns?: string[]; rows?: Record<string, unknown>[] }
        | null
      const hasRows = executed?.success && executed.columns && executed.rows
      const text = hasRows
        ? toMarkdownTable(executed!.columns!, executed!.rows!.map((r) => executed!.columns!.map((c) => String(r[c] ?? ''))))
        : schema.map((s) => `${s.name} (${s.type})${s.label ? ` — ${s.label}` : ''}`).join('\n')
      const ext = language === 'python' ? 'py' : language === 'sql' ? 'sql' : 'txt'
      if (!text) return null
      return (
        <div className="bg-white rounded border border-slate-200 overflow-hidden text-xs">
          <div className="bg-slate-50 px-2 py-1 text-[10px] text-slate-500 font-medium uppercase tracking-wide">
            Schema ({language}) — {schema.length} columns{hasRows ? ', query executed' : ''}
          </div>
          <ResultToolbar
            onCopy={() => navigator.clipboard.writeText(text)}
            onExport={() =>
              downloadBlob(hasRows ? 'query_result.csv' : `schema.${ext}`, hasRows ? toCSV(executed!.columns!, executed!.rows!) : text, hasRows ? 'text/csv' : 'text/plain')
            }
          />
        </div>
      )
    }

    case 'data_quality': {
      const columns = (result.columns as Record<string, Record<string, unknown>>) || {}
      const flags = (result.flags as Record<string, string[]>) || {}
      const names = Object.keys(columns)
      if (names.length === 0) return null
      const text = JSON.stringify({ row_count: result.row_count, columns, flags }, null, 2)
      return (
        <div className="bg-white rounded border border-slate-200 overflow-hidden text-xs">
          <div className="bg-slate-50 px-2 py-1 text-[10px] text-slate-500 font-medium uppercase tracking-wide">
            Data quality ({names.length} columns, {String(result.row_count)} rows)
          </div>
          <div className="p-2 space-y-1 max-h-[200px] overflow-y-auto">
            {names.slice(0, 10).map((name) => (
              <div key={name} className="flex items-center justify-between text-slate-700">
                <span className="font-mono">{name}</span>
                <span className="tabular-nums text-slate-500">
                  {Number(columns[name].missing_pct ?? 0).toFixed(1)}% missing
                </span>
              </div>
            ))}
          </div>
          {(flags.high_missing_columns?.length || flags.constant_columns?.length) && (
            <div className="px-2 pb-2 text-[10px] text-amber-600">
              {flags.high_missing_columns?.length ? `${flags.high_missing_columns.length} high-missing` : ''}
              {flags.high_missing_columns?.length && flags.constant_columns?.length ? ' · ' : ''}
              {flags.constant_columns?.length ? `${flags.constant_columns.length} constant` : ''}
            </div>
          )}
          <ResultToolbar
            onCopy={() => navigator.clipboard.writeText(text)}
            onExport={() => downloadBlob('data_quality.txt', text, 'text/plain')}
          />
        </div>
      )
    }

    case 'navigate_to': {
      const params = (result.params as Record<string, string>) || {}
      const tab = params.tab || 'Overview'
      const tableId = params.table_id
      return (
        <div className="flex items-center gap-2">
          <button
            onClick={() => onNavigate?.(tab, tableId)}
            className="inline-flex items-center gap-1.5 text-xs bg-primary-50 text-primary-600 px-2.5 py-1 rounded border border-primary-200 hover:bg-primary-100 transition-colors"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 18l6-6-6-6" />
            </svg>
            Go to {tab}{tableId ? ` › Table ${tableId}` : ''}
          </button>
        </div>
      )
    }

    default:
      return null
  }
}
