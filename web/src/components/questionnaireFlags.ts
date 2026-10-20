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
  QA_ISSUE: {
    label: 'QA found a problem',
    description:
      'The QA agent tested this question as a respondent sees it and against the document, and the programmer agent could not fix it safely. What it found is quoted below.',
    blocking: false,
    severity: 'warn',
  },
  BRIEF_RULE: {
    label: 'Study instruction applies',
    description:
      "The study's instructions state a rule about this question (quoted below). Check the question follows it — screen-outs, who is asked, how it is coded — then approve.",
    blocking: false,
    severity: 'warn',
  },
  MODEL_READ: {
    label: 'Read by a model — check it',
    description:
      'The rules could not read this question, so a model did. Its reading passed the same checks and can still be wrong in ways no check sees (a missing answer code, a wrong type). Compare it with the document line by line, then approve it. Bulk approval skips it.',
    blocking: false,
    severity: 'warn',
  },
  TYPE_NOT_SUPPORTED: {
    label: 'Kind of question not supported yet',
    description:
      'The question is a kind the survey link cannot run yet (for example a multi-answer or ranking grid). It is set up as the nearest kind it can run, and the real kind is kept on record. Script it by hand, or approve the stand-in if it is close enough.',
    blocking: false,
    severity: 'warn',
  },
  ENTRY_MODE_CONFLICT: {
    label: 'Typed number vs choice list',
    description:
      'The document says respondents type a number, but the question was read as a choice list. They produce different data columns: decide which one the instrument really is.',
    blocking: false,
    severity: 'warn',
  },
  DERIVED_VARIABLE_UNCONFIRMED: {
    label: 'Hidden variable — confirm',
    description:
      'Read as a hidden variable from its wording alone. Confirm it is not a real question somebody wrote in capitals.',
    blocking: false,
    severity: 'warn',
  },
  EXTRACTION_CONFIG_ERROR: {
    label: 'Extraction not configured',
    description:
      'The extractor could not run for this chunk because of a configuration fault (for example a missing key). Fix it once and re-read, or fill the question in by hand.',
    blocking: true,
    severity: 'error',
  },
  EXTRACTION_MODEL_ERROR: {
    label: 'Extraction output rejected',
    description:
      'The model answered but its output did not fit the question schema. Review the source text and fill this question in manually.',
    blocking: true,
    severity: 'error',
  },
  RECOVERED_FROM_DATAMAP: {
    label: 'Rebuilt from data map',
    description:
      'The document did not yield this question, so it was rebuilt from the data map. Its wording and routing are the script\'s, not the client\'s.',
    blocking: false,
    severity: 'warn',
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
