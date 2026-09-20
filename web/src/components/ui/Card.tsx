import { ReactNode } from 'react'

/** The "bg-white rounded-lg border border-slate-200" panel duplicated across
 * every screen, formalized in one place with the shared shadow token. */
export function Card({
  className = '',
  padded = true,
  children,
}: {
  className?: string
  padded?: boolean
  children: ReactNode
}) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white shadow-panel ${padded ? 'p-5' : ''} ${className}`}>
      {children}
    </div>
  )
}

/** The header bar a Card uses when it holds a titled section (table, list,
 * form) rather than free-form content. */
export function CardHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
      <div className="min-w-0">
        <h2 className="font-semibold text-slate-900">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}
