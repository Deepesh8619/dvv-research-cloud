import { ReactNode } from 'react'

/** A generic tag/label chip — for counts, categories, and flags that aren't
 * a pipeline status (StatusPill already owns that vocabulary). */
const TONES = {
  neutral: 'bg-slate-100 text-slate-600',
  accent: 'bg-primary-50 text-primary-600',
  info: 'bg-sky-50 text-sky-700',
  warning: 'bg-amber-50 text-amber-700',
  success: 'bg-emerald-50 text-emerald-700',
  danger: 'bg-red-50 text-red-700',
} as const

export default function Badge({
  children,
  tone = 'neutral',
  className = '',
}: {
  children: ReactNode
  tone?: keyof typeof TONES
  className?: string
}) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${TONES[tone]} ${className}`}>
      {children}
    </span>
  )
}
