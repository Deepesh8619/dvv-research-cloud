interface IconProps {
  className?: string
}

/** A single KPI number: icon chip, tabular-mono value, label. Used in KPI
 * rows (dashboard, fielding). Numbers in stat positions are always
 * tabular/mono so a live-updating count doesn't jitter the row width. */
export default function StatTile({
  label,
  value,
  icon: Icon,
  tint = 'text-primary-500 bg-primary-50',
  hint,
}: {
  label: string
  value: string | number
  icon?: (props: IconProps) => JSX.Element
  tint?: string
  hint?: string
}) {
  return (
    <div className="flex items-center gap-4 rounded-lg border border-slate-200 bg-white p-5 shadow-panel">
      {Icon && (
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${tint}`}>
          <Icon className="h-5 w-5" />
        </div>
      )}
      <div className="min-w-0">
        <p className="font-mono text-2xl font-semibold leading-none tabular-nums text-slate-900">{value}</p>
        <p className="mt-1.5 text-xs text-slate-500">{label}</p>
        {hint && <p className="mt-0.5 text-[11px] text-slate-400">{hint}</p>}
      </div>
    </div>
  )
}
