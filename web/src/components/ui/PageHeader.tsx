import { ReactNode } from 'react'

interface IconProps {
  className?: string
}

/**
 * The banner every main screen opens with: an icon chip, a title, a
 * one-line statement of what the screen is for, and right-aligned actions.
 * Replaces the bare `<h1> + <p>` pairs that used to open each page.
 */
export default function PageHeader({
  title,
  description,
  icon: Icon,
  actions,
  eyebrow,
}: {
  title: ReactNode
  description?: ReactNode
  icon?: (props: IconProps) => JSX.Element
  actions?: ReactNode
  eyebrow?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 rounded-lg border border-slate-200 bg-white p-5 shadow-panel sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-start gap-3.5">
        {Icon && (
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary-50 text-primary-500">
            <Icon className="h-5 w-5" />
          </div>
        )}
        <div className="min-w-0">
          {eyebrow && (
            <p className="mb-0.5 text-xs font-medium uppercase tracking-wide text-slate-400">{eyebrow}</p>
          )}
          <h1 className="truncate text-xl font-semibold leading-tight text-slate-900 sm:text-2xl">{title}</h1>
          {description && <p className="mt-1 text-sm text-slate-500">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
