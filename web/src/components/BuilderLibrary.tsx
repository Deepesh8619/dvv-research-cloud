import { useEffect, useState } from 'react'

import { questionnaireApi, type LibraryEntry, type LibraryField } from '../api/client'

/**
 * Study templates and ready-made blocks for building a questionnaire from
 * scratch. Each block is written the standard way — balanced labelled scales,
 * "None of these" exclusive, brand lists randomised, its routing included —
 * and asks only for what it cannot know: the category, the brands, the
 * attributes.
 */
const field = 'mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm'

export function useLibrary() {
  const [lib, setLib] = useState<{ modules: LibraryEntry[]; templates: LibraryEntry[] } | null>(null)
  useEffect(() => { questionnaireApi.library().then((r) => setLib(r.data)).catch(() => setLib(null)) }, [])
  return lib
}

export function InputsForm({ fields, values, onChange }: {
  fields: LibraryField[]
  values: Record<string, string>
  onChange: (v: Record<string, string>) => void
}) {
  if (fields.length === 0) return <p className="text-sm text-slate-500">Nothing to fill in.</p>
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {fields.map((f) => (
        <label key={f.key} className={`text-xs font-medium text-slate-600 ${f.kind === 'lines' || f.kind === 'long' ? 'sm:col-span-2' : ''}`}>
          {f.label}
          {f.kind === 'lines' || f.kind === 'long' ? (
            <textarea rows={f.kind === 'lines' ? 4 : 3} className={field} placeholder={f.placeholder}
              value={values[f.key] ?? ''} onChange={(e) => onChange({ ...values, [f.key]: e.target.value })} />
          ) : (
            <input className={field} placeholder={f.placeholder} inputMode={f.kind === 'number' ? 'numeric' : undefined}
              value={values[f.key] ?? ''} onChange={(e) => onChange({ ...values, [f.key]: e.target.value })} />
          )}
        </label>
      ))}
    </div>
  )
}

/** "Start from": a blank survey or a study template, as cards. */
export function TemplatePicker({ templates, chosen, onChoose }: {
  templates: LibraryEntry[]
  chosen: string | null
  onChoose: (id: string | null) => void
}) {
  const card = (on: boolean) =>
    `rounded-lg border p-3 text-left transition-colors ${on ? 'border-slate-900 ring-1 ring-slate-900' : 'border-slate-200 hover:border-slate-400'}`
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" role="radiogroup" aria-label="Start from">
      <button type="button" role="radio" aria-checked={chosen === null} className={card(chosen === null)} onClick={() => onChoose(null)}>
        <span className="block text-sm font-medium text-slate-900">Blank</span>
        <span className="mt-1 block text-xs text-slate-500">Add questions and blocks yourself.</span>
      </button>
      {templates.map((t) => (
        <button key={t.id} type="button" role="radio" aria-checked={chosen === t.id} className={card(chosen === t.id)}
          onClick={() => onChoose(t.id)}>
          <span className="block text-sm font-medium text-slate-900">{t.title}</span>
          <span className="mt-1 block text-xs text-slate-500">{t.description}</span>
        </button>
      ))}
    </div>
  )
}

/** The ready-made blocks, added after the selected question. */
export function ModulePicker({ modules, onAdd }: {
  modules: LibraryEntry[]
  onAdd: (module: string, inputs: Record<string, string>) => Promise<string | null>
}) {
  const [open, setOpen] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const current = modules.find((m) => m.id === open)

  return (
    <details className="group" open={!!current}>
      <summary className="cursor-pointer list-none text-xs font-medium uppercase tracking-wider text-slate-500 hover:text-slate-700">
        <span className="mr-1 inline-block transition-transform group-open:rotate-90" aria-hidden>▸</span>
        Add a block ({modules.length})
      </summary>
      <div className="mt-2">
      {!current ? (
        <div className="grid gap-1.5">
          {modules.map((m) => (
            <button key={m.id} type="button" onClick={() => { setOpen(m.id); setValues({}); setError(null) }}
              className="rounded-md border border-slate-200 px-2.5 py-2 text-left hover:border-slate-400">
              <span className="block text-sm font-medium text-slate-900">{m.title}</span>
              <span className="block text-xs text-slate-500">{m.description}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="space-y-3 rounded-md border border-slate-200 p-3">
          <div>
            <p className="text-sm font-medium text-slate-900">{current.title}</p>
            <p className="text-xs text-slate-500">{current.description}</p>
          </div>
          <InputsForm fields={current.inputs} values={values} onChange={setValues} />
          {error && <p className="text-sm text-red-700">{error}</p>}
          <div className="flex gap-2">
            <button type="button" disabled={busy} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              onClick={async () => {
                setBusy(true)
                const err = await onAdd(current.id, values)
                setBusy(false)
                if (err) setError(err)
                else setOpen(null)
              }}>
              {busy ? 'Adding…' : 'Add block'}
            </button>
            <button type="button" className="rounded-md px-3 py-1.5 text-sm text-slate-600" onClick={() => setOpen(null)}>Cancel</button>
          </div>
        </div>
      )}
      </div>
    </details>
  )
}
