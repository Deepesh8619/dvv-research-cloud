import { TYPE_CARDS } from './typeMeta'
import { TypeIcon } from './TypeIcon'

/**
 * One card per addable type, replacing a plain <select>. A researcher
 * building from scratch recognises "Rating grid" as a shape faster than as
 * one of eight alphabetically-sorted options in a dropdown.
 */
export function AddQuestionPalette({
  onPick,
  disabled,
}: {
  onPick: (key: string) => void
  disabled?: boolean
}) {
  return (
    <div role="group" aria-label="Add a question" className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
      {TYPE_CARDS.map((c) => (
        <button
          key={c.key}
          type="button"
          disabled={disabled}
          onClick={() => onPick(c.key)}
          className="flex flex-col items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 py-3 text-center transition-all duration-150 hover:border-primary-300 hover:bg-primary-50/60 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
        >
          <TypeIcon type={c.key} className="h-5 w-5 text-slate-500" />
          <span className="text-xs font-medium leading-tight text-slate-700">{c.hint}</span>
        </button>
      ))}
    </div>
  )
}
