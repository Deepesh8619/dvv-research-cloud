/**
 * Why the system read this question the way it did.
 *
 * A reviewer approving eighty questions cannot re-read the source document for
 * each one. Without a citation, "Approve" is a rubber stamp: the reviewer is
 * agreeing to something they have no cheap way to check. This panel names the
 * path taken and quotes the exact fragment behind each field, so approving is
 * checking a citation — and a wrong reading is visible at a glance rather than
 * discovered weeks later in the data.
 */
interface Props {
  // Both are optional on purpose: questions extracted before provenance
  // existed carry neither, and a review screen must degrade to "no citation
  // available" rather than crash on older data.
  method?: string | null
  evidence?: Record<string, string> | null
}

const METHOD_LABELS: Record<string, { label: string; detail: string }> = {
  spec_table: {
    label: 'Read from the spec table',
    detail: 'Taken from the labelled rows in the document. No AI involved.',
  },
  prose_directives: {
    label: 'Read from the scripter notes',
    detail: 'Taken from the wording and bracketed directives. No AI involved.',
  },
  llm: {
    label: 'Read by the AI model',
    detail: 'The document did not state this plainly, so a model was asked. Check it closely.',
  },
}

/** Field keys in the order a reviewer reads the form. */
const FIELD_LABELS: Record<string, string> = {
  type: 'Answer type',
  terminate: 'Screen-out',
  base: 'Who is asked',
  randomize: 'Randomisation',
}

export default function ExtractionProvenance({ method, evidence }: Props) {
  const info = METHOD_LABELS[method ?? '']
  const cites = evidence ?? {}
  const cited = Object.keys(FIELD_LABELS).filter((f) => cites[f])
  if (!info && cited.length === 0) return null

  // An AI reading is the one a reviewer must not skim, so it is the only one
  // tinted. The deterministic paths stay quiet — they are the normal case.
  const isModel = method === 'llm'

  return (
    <section
      aria-label="How this question was read"
      className={`rounded-md border px-3 py-2.5 ${
        isModel ? 'border-amber-200 bg-amber-50' : 'border-slate-200 bg-slate-50'
      }`}
    >
      {info && (
        <>
          <h3
            className={`text-xs font-semibold uppercase tracking-wide ${
              isModel ? 'text-amber-800' : 'text-slate-600'
            }`}
          >
            {info.label}
          </h3>
          <p className={`text-xs mt-0.5 ${isModel ? 'text-amber-700' : 'text-slate-500'}`}>
            {info.detail}
          </p>
        </>
      )}

      {cited.length > 0 && (
        <dl className="mt-2 space-y-1">
          {cited.map((f) => (
            <div key={f} className="flex gap-2 text-xs items-baseline">
              <dt className="text-slate-500 shrink-0 w-28">{FIELD_LABELS[f]}</dt>
              <dd className="font-mono text-slate-700 break-words min-w-0">{cites[f]}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  )
}
