import { ReactNode } from 'react'

interface IconProps {
  className?: string
}

/** The "nothing here yet" panel — icon, one-line title, a short explanation,
 * an optional action. Same shape as the ad hoc empty states that used to be
 * hand-rolled per page (dashboard, projects list, …). */
export default function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon?: (props: IconProps) => JSX.Element
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      {Icon && (
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-primary-50 text-primary-500">
          <Icon className="h-6 w-6" />
        </div>
      )}
      <p className="text-sm font-medium text-slate-900">{title}</p>
      {description && <p className="mb-5 mt-1 max-w-xs text-sm text-slate-500">{description}</p>}
      {action}
    </div>
  )
}
