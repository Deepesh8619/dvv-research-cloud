import { ButtonHTMLAttributes, forwardRef } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-primary-500 text-white hover:bg-primary-600 focus-visible:ring-primary-500',
  secondary: 'bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 focus-visible:ring-primary-500',
  ghost: 'text-slate-600 hover:bg-slate-100 focus-visible:ring-primary-500',
  danger: 'bg-red-600 text-white hover:bg-red-700 focus-visible:ring-red-500',
  success: 'bg-emerald-600 text-white hover:bg-emerald-700 focus-visible:ring-emerald-500',
}

/** Shared button styling — one shape, one motion, one focus ring, a ~44px
 * touch target, reused as a className builder so it works on <button>,
 * <Link>, and <a> alike. */
export function buttonClasses(variant: Variant = 'primary', className = ''): string {
  return `inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-all duration-150 ease-out active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none disabled:active:scale-100 ${VARIANTS[variant]} ${className}`
}

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }>(
  ({ variant = 'primary', className = '', type = 'button', ...props }, ref) => (
    <button ref={ref} type={type} className={buttonClasses(variant, className)} {...props} />
  ),
)
Button.displayName = 'Button'
