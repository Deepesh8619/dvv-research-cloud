// Human-readable descriptions for questionnaire extraction flags.
// Source of truth for the codes: src/engines/questionnaire/schema.py.
// _BLOCKING_FLAGS in api/routes/questionnaire.py must be clear (or the question
// approved past them) before approve-all can succeed.

export interface FlagInfo {
  label: string
  description: string
  /** Blocks approve-all until resolved (matches backend _BLOCKING_FLAGS). */
  blocking: boolean
  severity: 'error' | 'warn'
}

export const FLAG_INFO: Record<string, FlagInfo> = {
  AMBIGUOUS_ROUTING: {
    label: 'Ambiguous routing',
    description:
      'The source mentions skip/show logic but did not clearly name which prior question and answer it depends on. Confirm and set the routing manually.',
    blocking: true,
    severity: 'error',
  },
  UNRESOLVED_ROUTING_QID: {
    label: 'Unresolved routing question',
    description:
      'Routing references a question id that could not be matched to any extracted question. Fix the reference.',
    blocking: true,
    severity: 'error',
  },
  EXTRACTION_UNAVAILABLE: {
    label: 'Extraction unavailable',
    description:
      'The extractor could not process this chunk (model degraded/unavailable). Review the source text and fill this question in manually.',
    blocking: true,
    severity: 'error',
  },
  DUPLICATE_OPTION_CODE: {
    label: 'Duplicate option code',
    description: 'Two answer options share the same code. Give each option a distinct code.',
    blocking: false,
    severity: 'warn',
  },
  EMPTY_OPTIONS: {
    label: 'Empty options',
    description: 'A choice question was extracted with no answer options. Add the options from the source.',
    blocking: false,
    severity: 'warn',
  },
  VERIFICATION_DISCREPANCY: {
    label: 'Verification discrepancy',
    description:
      'The second-pass verifier found the structured extraction may not faithfully match the source (e.g. invented/missing option, wrong type). Compare against source text.',
    blocking: false,
    severity: 'warn',
  },
  VERIFICATION_UNAVAILABLE: {
    label: 'Verification unavailable',
    description: 'The verification pass could not run for this question. Manually confirm it matches the source.',
    blocking: false,
    severity: 'warn',
  },
  DERIVED_VARIABLE: {
    label: 'Derived / hidden variable',
    description:
      'This is a hidden variable (HID_ prefix) computed by the programmer from other answers, not a respondent-facing question. It has no routing to extract — set up its derivation manually.',
    blocking: false,
    severity: 'warn',
  },
  TRADE_OFF_UNSUPPORTED: {
    label: 'Trade-off exercise — not supported',
    description:
      'This is a MaxDiff or conjoint block: an experimental design, not a question. Each respondent should see a different subset of items generated from a balanced design, and the result is a utility model rather than counts — neither of which exists here. Fielded as it stands, every respondent would see the same static screen and the export could not be analysed. Script this block in a tool that does trade-off designs. Approving the question clears the flag and fields the rest of the survey without it.',
    blocking: true,
    severity: 'error',
  },
  LIST_INHERITED: {
    label: 'List copied — check',
    description:
      'The wording points at a list ("each of these brands") without naming it, so the nearest earlier list of that kind was copied in. It decides the data columns: confirm it is the right one, or edit the rows.',
    blocking: false,
    severity: 'warn',
  },
}

export function flagInfo(code: string): FlagInfo {
  return (
    FLAG_INFO[code] || {
      label: code.replace(/_/g, ' ').toLowerCase(),
      description: 'Extraction flag requiring review.',
      blocking: false,
      severity: 'warn',
    }
  )
}
