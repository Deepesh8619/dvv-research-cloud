// The kinds of variable a questionnaire holds — one list, shared by the review
// screen and the field report so the two never disagree about what a bucket is.
// Order is how a respondent meets them. Mirrors src/engines/questionnaire/buckets.py.

export interface BucketInfo {
  key: string
  label: string
  hint: string
  /** Tailwind classes: the section dot / row stripe, and a soft chip. */
  dot: string
  chip: string
}

export const BUCKETS: BucketInfo[] = [
  { key: 'screener', label: 'Screener', hint: 'Who got in', dot: 'bg-amber-400', chip: 'bg-amber-50 text-amber-800 ring-amber-200' },
  { key: 'profile', label: 'Profile', hint: 'Who they are — what tables are cut by', dot: 'bg-sky-400', chip: 'bg-sky-50 text-sky-800 ring-sky-200' },
  { key: 'core', label: 'Core measures', hint: 'What the study measures', dot: 'bg-indigo-400', chip: 'bg-indigo-50 text-indigo-800 ring-indigo-200' },
  { key: 'loop', label: 'Brand loops', hint: 'Asked once per brand', dot: 'bg-emerald-400', chip: 'bg-emerald-50 text-emerald-800 ring-emerald-200' },
  { key: 'open_end', label: 'Open-ends', hint: 'What people wrote', dot: 'bg-violet-400', chip: 'bg-violet-50 text-violet-800 ring-violet-200' },
  { key: 'hidden', label: 'Hidden', hint: 'Computed or assigned, never asked', dot: 'bg-slate-400', chip: 'bg-slate-100 text-slate-700 ring-slate-200' },
]

export function bucketInfo(key: string | undefined): BucketInfo | undefined {
  return BUCKETS.find((b) => b.key === key)
}
