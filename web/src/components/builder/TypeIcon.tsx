import { ChatIcon, GridIcon, InfoIcon, ListIcon, TableIcon } from '../Icons'

function RadioIcon({ className }: { className: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />
    </svg>
  )
}

function CheckboxIcon({ className }: { className: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="M8 12.5l2.5 2.5L16 9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function NumberIcon({ className }: { className: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M9 4L7 20M17 4l-2 16M4 9h16M3 15h16" strokeLinecap="round" />
    </svg>
  )
}

/**
 * One glyph per authorable question type. The palette and the question list
 * share it, so a card and the row it produces always agree on what it looks
 * like. Reuses the app's existing icon set where a shape already fits
 * (grid/list/chat/info/table); only single, multi and number needed a new one.
 */
export function TypeIcon({ type, className = 'w-5 h-5' }: { type: string; className?: string }) {
  switch (type) {
    case 'single':
      return <RadioIcon className={className} />
    case 'multi':
      return <CheckboxIcon className={className} />
    case 'grid_single':
      return <GridIcon className={className} />
    case 'rank':
      return <ListIcon className={className} />
    case 'numeric':
      return <NumberIcon className={className} />
    case 'open_text':
      return <ChatIcon className={className} />
    case 'constant_sum':
      return <TableIcon className={className} />
    case 'info':
      return <InfoIcon className={className} />
    case 'dropdown':
      return <ListIcon className={className} />
    default:
      return <GridIcon className={className} />
  }
}
