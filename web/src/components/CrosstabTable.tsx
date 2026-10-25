export interface TabColumn {
  label: string
  letter: string | null
  base: number
  unweighted_base: number
  effective_base: number
}

export interface TabRow {
  label: string
  pct: number[]
  sig: string[]
  kind: 'category' | 'net'
}

export interface TabTable {
  id: string
  stub: string
  title: string
  filter: string
  columns: TabColumn[]
  rows: TabRow[]
  means: number[] | null
  mean_sig: string[] | null
}

interface Props {
  table: TabTable
  minBase: number
  showBases: boolean
  /** When given, each cell is a button that asks where its number came from. */
  onCellClick?: (row: number, col: number) => void
  selected?: { row: number; col: number } | null
}

export default function CrosstabTable({ table, minBase, showBases, onCellClick, selected }: Props) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr>
            <th className="sticky left-0 bg-white text-left align-bottom pb-2 pr-4 min-w-[220px] font-normal text-slate-400 text-xs">
              Column %
            </th>
            {table.columns.map((col, j) => (
              <th key={j} className="bg-primary-500 text-white font-semibold px-3 py-2.5 text-center min-w-[92px] border-l border-white/10">
                <div>
                  {col.label}
                  {col.letter && col.effective_base < minBase && (
                    <span className="text-amber-300" title={`Low base (effective n=${col.effective_base} < ${minBase}): not sig-tested`}> *</span>
                  )}
                </div>
                <div className="text-[10px] font-normal text-white/60 mt-0.5">
                  {col.letter ? `(${col.letter})` : ' '}
                </div>
              </th>
            ))}
          </tr>
          {showBases && (
            <>
              <tr className="text-xs text-slate-500 italic">
                <td className="sticky left-0 bg-white py-1.5 pr-4">Base: weighted</td>
                {table.columns.map((col, j) => (
                  <td key={j} className="text-center py-1.5 bg-slate-50 font-mono tabular-nums">{Math.round(col.base)}</td>
                ))}
              </tr>
              <tr className="text-xs text-slate-500 italic border-b border-slate-200">
                <td className="sticky left-0 bg-white py-1.5 pr-4">Base: unweighted</td>
                {table.columns.map((col, j) => (
                  <td key={j} className="text-center py-1.5 bg-slate-50 font-mono tabular-nums">{col.unweighted_base}</td>
                ))}
              </tr>
            </>
          )}
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr
              key={i}
              className={`border-b border-slate-100 ${row.kind === 'net' ? 'bg-slate-50 font-semibold' : 'hover:bg-sky-50/40'}`}
            >
              <td className="sticky left-0 bg-inherit py-2 pr-4 text-slate-800">{row.label}</td>
              {row.pct.map((pct, j) => (
                <td
                  key={j}
                  className={`text-center py-2 font-mono tabular-nums ${selected?.row === i && selected.col === j ? 'bg-amber-100 ring-2 ring-inset ring-amber-500' : ''}`}
                >
                  {(() => {
                    const value = (
                      <span
                        title={
                          row.sig[j]
                            ? `Significantly higher than column(s) ${row.sig[j].split('').join(', ')} at 95% confidence`
                            : undefined
                        }
                      >
                        {pct.toFixed(1)}
                        {row.sig[j] && (
                          <sup className="text-emerald-600 font-bold ml-0.5 cursor-help">{row.sig[j]}</sup>
                        )}
                      </span>
                    )
                    return onCellClick ? (
                      <button
                        type="button"
                        onClick={() => onCellClick(i, j)}
                        aria-label={`Where does ${pct.toFixed(1)}% for ${row.label}, ${table.columns[j].label} come from?`}
                        className="rounded px-1 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
                      >
                        {value}
                      </button>
                    ) : (
                      value
                    )
                  })()}
                </td>
              ))}
            </tr>
          ))}
          {table.means && (
            <tr className="border-t border-slate-300 italic text-slate-700">
              <td className="sticky left-0 bg-white py-2 pr-4 font-semibold">Mean</td>
              {table.means.map((m, j) => (
                <td key={j} className="text-center py-2 font-mono tabular-nums font-medium">
                  {m.toFixed(2)}
                  {table.mean_sig?.[j] && (
                    <sup className="text-emerald-600 font-bold ml-0.5">{table.mean_sig[j]}</sup>
                  )}
                </td>
              ))}
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
