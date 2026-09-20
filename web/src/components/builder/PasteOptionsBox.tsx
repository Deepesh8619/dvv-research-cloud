import { useState } from 'react'
import { QuestionOption } from '../../api/client'
import { parsePastedOptions } from './typeMeta'

/**
 * Turns pasted lines into coded options in one step, instead of "+ Add
 * option" clicked once per line. Accepts a bare list, or "1 Label" /
 * "1. Label" / "1\tLabel" when the source already carries codes.
 */
export function PasteOptionsBox({ onApply }: { onApply: (options: QuestionOption[]) => void }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const parsed = parsePastedOptions(text)

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className="text-xs font-medium text-primary-500 hover:text-primary-600">
        Paste a list…
      </button>
    )
  }

  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-2">
      <label htmlFor="paste-options" className="mb-1 block text-xs font-medium text-slate-600">
        One option per line — &quot;1 Label&quot;, &quot;1. Label&quot; or &quot;1{'\t'}Label&quot; keeps its code
      </label>
      <textarea
        id="paste-options"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        placeholder={'1 Strongly agree\n2 Agree\n3 Disagree'}
        className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
      />
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          disabled={parsed.length === 0}
          onClick={() => {
            onApply(parsed)
            setText('')
            setOpen(false)
          }}
          className="rounded-md bg-primary-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-600 disabled:opacity-40"
        >
          Add {parsed.length || ''} option{parsed.length === 1 ? '' : 's'}
        </button>
        <button
          type="button"
          onClick={() => {
            setOpen(false)
            setText('')
          }}
          className="text-xs text-slate-500 hover:text-slate-700"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
