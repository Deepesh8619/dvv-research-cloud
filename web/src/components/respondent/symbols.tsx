/**
 * The mark drawn above each question, named by the backend from the
 * questionnaire's own wording (see src/engines/questionnaire/symbols.py).
 *
 * Drawn, never fetched: a survey on a bad connection looks the same as one on
 * fibre, and no respondent's browser calls a third party mid-interview. One
 * stroke weight across every mark, so a screen never looks assembled from
 * different sets.
 */

type Props = { className?: string }

const S = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round' } as const

function Mark({ className = 'h-5 w-5', children }: Props & { children: React.ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" {...S}>
      {children}
    </svg>
  )
}

/** Every name `symbols.py` can emit. A missing one falls back to `list`. */
export const SYMBOLS: Record<string, (p: Props) => JSX.Element> = {
  pharmacy: (p) => <Mark {...p}><path d="M12 7v10M7 12h10" /><rect x="3.5" y="3.5" width="17" height="17" rx="4" /></Mark>,
  health: (p) => <Mark {...p}><path d="M3 12h4l2-5 3 10 2.5-6 1.5 3h5" /></Mark>,
  insurance: (p) => <Mark {...p}><path d="M12 3l7.5 3v6c0 4.4-3.1 8-7.5 9.5C7.6 20 4.5 16.4 4.5 12V6z" /><path d="M9.2 12l2 2 3.6-3.8" /></Mark>,
  banking: (p) => <Mark {...p}><path d="M3 9.5L12 4l9 5.5" /><path d="M5.5 10.5v7M12 10.5v7M18.5 10.5v7M3.5 20.5h17" /></Mark>,
  beauty: (p) => <Mark {...p}><path d="M9 3.5h6l-.8 3.2H9.8z" /><rect x="7.5" y="6.7" width="9" height="13.8" rx="3" /><path d="M10.5 12h3" /></Mark>,
  food: (p) => <Mark {...p}><path d="M7 3.5v8M4.6 3.5v4.2a2.4 2.4 0 004.8 0V3.5" /><path d="M7 11.5v9" /><path d="M17 20.5v-17c-2.2 1-3 4-3 7 0 2 .8 3.2 3 3.4" /></Mark>,
  drink: (p) => <Mark {...p}><path d="M6 4h12l-1.4 15.2a1.6 1.6 0 01-1.6 1.3H9a1.6 1.6 0 01-1.6-1.3z" /><path d="M6.6 9.5h10.8" /></Mark>,
  vehicle: (p) => <Mark {...p}><path d="M4 16.5v-3l2-5h12l2 5v3" /><path d="M3 16.5h18" /><circle cx="7.5" cy="18.2" r="1.8" /><circle cx="16.5" cy="18.2" r="1.8" /></Mark>,
  travel: (p) => <Mark {...p}><path d="M3 13.5l18-6.5-4 9-4.5-1.5L9 19l-1-5.2z" /></Mark>,
  device: (p) => <Mark {...p}><rect x="6.5" y="2.5" width="11" height="19" rx="3" /><path d="M10.5 18.5h3" /></Mark>,
  shopping: (p) => <Mark {...p}><path d="M5.5 7.5h13l-1.2 12H6.7z" /><path d="M9 10V6.5a3 3 0 016 0V10" /></Mark>,
  money: (p) => <Mark {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 7v10M14.6 9.3c-.6-.9-1.6-1.3-2.6-1.3-1.5 0-2.7.8-2.7 2s1.2 1.8 2.7 2 2.7.8 2.7 2-1.2 2-2.7 2c-1.1 0-2.1-.5-2.6-1.3" /></Mark>,
  recommend: (p) => <Mark {...p}><path d="M7 21V10.5l4-7c1.3 0 2.2 1 2.2 2.3L12.6 10H18a2 2 0 012 2.3l-1.2 6.4A2.6 2.6 0 0116.2 21z" /><path d="M7 10.5H4.5V21H7" /></Mark>,
  brand: (p) => <Mark {...p}><path d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8-5.3-2.8-5.3 2.8 1-5.8L3.5 9.7l5.9-.9z" /></Mark>,
  media: (p) => <Mark {...p}><path d="M4 9.5h3.5L14 5v14l-6.5-4.5H4z" /><path d="M17.5 9c1.2 1.6 1.2 4.4 0 6" /></Mark>,
  household: (p) => <Mark {...p}><path d="M4 11L12 4l8 7" /><path d="M6.2 10.2V20h11.6v-9.8" /><path d="M10 20v-5h4v5" /></Mark>,
  child: (p) => <Mark {...p}><circle cx="12" cy="8" r="4" /><path d="M5 20.5c1-3.6 3.7-5.5 7-5.5s6 1.9 7 5.5" /><path d="M9.5 7.6h.01M14.5 7.6h.01" /></Mark>,
  pet: (p) => <Mark {...p}><ellipse cx="12" cy="16" rx="4" ry="3.4" /><ellipse cx="6" cy="10.5" rx="2" ry="2.6" /><ellipse cx="18" cy="10.5" rx="2" ry="2.6" /><ellipse cx="9.5" cy="6.5" rx="1.9" ry="2.5" /><ellipse cx="14.5" cy="6.5" rx="1.9" ry="2.5" /></Mark>,
  work: (p) => <Mark {...p}><rect x="3" y="7.5" width="18" height="12.5" rx="2.5" /><path d="M9 7.5V6a2 2 0 012-2h2a2 2 0 012 2v1.5" /><path d="M3 13h18" /></Mark>,
  place: (p) => <Mark {...p}><path d="M12 21c4-4.6 6-8 6-10.6A6 6 0 006 10.4C6 13 8 16.4 12 21z" /><circle cx="12" cy="10.3" r="2.3" /></Mark>,
  person: (p) => <Mark {...p}><circle cx="12" cy="8" r="3.8" /><path d="M4.8 20.5c.7-3.9 3.6-6 7.2-6s6.5 2.1 7.2 6" /></Mark>,
  time: (p) => <Mark {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 6.8V12l3.4 2" /></Mark>,
  star: (p) => <Mark {...p}><path d="M12 4l2.5 5.1 5.5.8-4 3.9 1 5.6-5-2.7-5 2.7 1-5.6-4-3.9 5.5-.8z" /></Mark>,
  agree: (p) => <Mark {...p}><path d="M4 12h16" /><circle cx="15.5" cy="12" r="3" /><path d="M4.5 7h5M4.5 17h5" /></Mark>,
  list: (p) => <Mark {...p}><path d="M9 7h11M9 12h11M9 17h11" /><path d="M4.6 7h.01M4.6 12h.01M4.6 17h.01" /></Mark>,
  checklist: (p) => <Mark {...p}><path d="M10.5 7.5H20M10.5 16.5H20" /><path d="M3.6 7.3l1.6 1.6 2.4-2.8M3.6 16.3l1.6 1.6 2.4-2.8" /></Mark>,
  grid: (p) => <Mark {...p}><rect x="3.5" y="3.5" width="17" height="17" rx="2.5" /><path d="M3.5 9.2h17M3.5 14.8h17M9.2 3.5v17" /></Mark>,
  number: (p) => <Mark {...p}><path d="M9.2 4l-1.6 16M16.4 4l-1.6 16" /><path d="M4.5 9h15.5M3.9 15h15.5" /></Mark>,
  rank: (p) => <Mark {...p}><path d="M4 18.5V11h4v7.5M10 18.5V5.5h4v13M16 18.5v-5h4v5" /><path d="M3 20.5h18" /></Mark>,
  share: (p) => <Mark {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5V12l6 4.2" /></Mark>,
  comment: (p) => <Mark {...p}><path d="M20.5 13.5a3 3 0 01-3 3H9l-4.5 3.5v-3.5a3 3 0 01-1-2.2V7a3 3 0 013-3h11a3 3 0 013 3z" /></Mark>,
}

/**
 * The mark for one question, in a tinted chip.
 *
 * Deliberately quiet: the chip is the accent at low opacity, the mark is one
 * stroke weight, and it sits above the wording rather than beside it so it
 * never competes with the question for the first line of a phone screen.
 */
export default function QuestionSymbol({ name, accent }: { name?: string; accent: string }) {
  const Draw = (name && SYMBOLS[name]) || null
  if (!Draw) return null
  return (
    <span
      className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-xl"
      style={{ background: `${accent}14`, color: accent }}
      data-symbol={name}
    >
      <Draw className="h-5 w-5" />
    </span>
  )
}
