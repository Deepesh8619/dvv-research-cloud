"""Read the scripter directives in a PROSE questionnaire, without an LLM.

Prose questionnaires have no table structure, so the spec-table parser does
nothing for them — and two of the three real client documents are prose. But
they are not unstructured. The conventions that carry the fields a reviewer
actually needs are bracketed and unambiguous:

    S2.  Do you, or does anyone in your household, work in any of these?
    Select all that apply.                      <- the TYPE
    [PN: RANDOMIZE]                             <- a scripter directive
    Advertising or market research [TERMINATE]  <- a screen-out, per option
    Healthcare
    None of these [ANCHOR, EXCLUSIVE]           <- option flags

Measured across both prose client documents: bracketed programmer notes appear
on 53% and 40% of questions, RANDOMIZE on 38% and 44%, an explicit type
instruction on 30% and 25%. Where these appear they are exact — a `[TERMINATE]`
tag is not a judgement call.

An earlier pass concluded prose "cannot be parsed deterministically". That was
wrong, and wrong for an avoidable reason: it searched for "please select
one/all" and numbered option lines, neither of which these documents use. The
signals were there; the patterns were not.

Same conservative contract as spec_table.py: anything uncertain is left None
and named in `missing`, so the caller escalates to a model. A wrong
deterministic answer is silent and looks authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.core.survey_definition import Expression, Option, QuestionType

# A leading question label: "S2.", "Q12a)", "P1:" — the segmenter already found
# it, but the text still carries it and it is not part of the wording.
_LABEL_RE = re.compile(r"^\s*([A-Za-z]{0,3}\d+[a-z]?)\s*[.):]\s*")

# A whole line that is only a scripter directive, e.g. "[PN: RANDOMIZE]".
_DIRECTIVE_LINE_RE = re.compile(r"^\s*\[(?P<body>[^\]]*)\]\s*$")

# A trailing tag on an option: "Healthcare [TERMINATE]", "None [ANCHOR, EXCLUSIVE]".
_OPTION_TAG_RE = re.compile(r"\s*\[(?P<tags>[^\]]*)\]\s*$")

# Type instructions, most specific first — "select all that apply" must beat a
# bare "select".
_TYPE_PATTERNS: list[tuple[re.Pattern, QuestionType]] = [
    (re.compile(r"select all that apply|choose all that apply|multiple answers",
                re.I), QuestionType.MULTI),
    (re.compile(r"select one|choose one|single answer|select your", re.I),
     QuestionType.SINGLE),
    (re.compile(r"enter (a )?number|numeric|\byears\b|"
                r"\bage\b.*\bnumber\b|number\s+\d+\s*-\s*\d+", re.I),
     QuestionType.NUMERIC),
    (re.compile(r"open[- ]?end|please describe|in your own words|type your|"
                r"enter response|verbatim", re.I), QuestionType.OPEN_TEXT),
]

# Tags carried by an individual option.
_TERMINATE_TAG = re.compile(r"\bTERMINATE\b|\bSCREEN\s*-?\s*OUT\b", re.I)
_EXCLUSIVE_TAG = re.compile(r"\bEXCLUSIVE\b", re.I)
_ANCHOR_TAG = re.compile(r"\bANCHOR\b", re.I)

# A line that continues the question rather than starting the answer list —
# these read as instructions to the respondent, not as options.
_INSTRUCTION_RE = re.compile(
    r"^(select|choose|please|enter|type)\b", re.IGNORECASE
)

# Routing prose that belongs to the NEXT section, not this question's options
# ("Adult Consumer Path Ages 18-25 (IF S0=1 AND S1=18-25) ASK S3-S6").
_SECTION_ROUTING_RE = re.compile(r"\bASK\s+[A-Z]{0,3}\d|\bIF\s+[A-Z]{1,3}\d+\s*=", re.I)


@dataclass
class ProseFields:
    """What a prose question's own text states. `missing` drives escalation."""

    qid: str
    text: str | None = None
    qtype: QuestionType | None = None
    options: list[Option] = field(default_factory=list)
    terminate_if: Expression | None = None
    terminate_verbatim: str | None = None
    randomize: bool = False
    # Directives we recognised but do not model yet — kept visible, never
    # silently dropped, so an unhandled convention is discoverable.
    directives: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing


def _strip_label(line: str) -> str:
    return _LABEL_RE.sub("", line, count=1).strip()


def _infer_type(
    text: str, options: list[Option], directives: list[str] | None = None
) -> QuestionType | None:
    """Type from the instruction line OR a scripter directive.

    Measured across all three real client documents, an unstated type was the
    single biggest reason a question still needed a model: missing on 63, 57
    and 56 questions. The reason was avoidable — the declaration is often
    inside the bracketed note rather than on the instruction line:

        [PN: OPEN END]                        -> open text
        [ENTER NUMBER 1-100]                  -> numeric
        [PN: OPEN END. ALLOW UP TO 10 RESPONSES]

    Reading both halves is what turns those into free answers.
    """
    for directive in directives or []:
        for pattern, qtype in _TYPE_PATTERNS:
            if pattern.search(directive):
                # A directive is an explicit scripter instruction, so it is
                # trusted even without options — "[PN: OPEN END]" IS the type.
                if qtype in (QuestionType.MULTI, QuestionType.SINGLE) and not options:
                    continue
                return qtype
    for pattern, qtype in _TYPE_PATTERNS:
        if pattern.search(text):
            # "Select all that apply" with no options is a contradiction; trust
            # the instruction only when the shape agrees with it.
            if qtype in (QuestionType.MULTI, QuestionType.SINGLE) and not options:
                continue
            return qtype
    if options:
        # Options but no instruction: single vs multi changes the data
        # structure, so this is a real question for a model, not a guess.
        return None
    return None


def parse_prose_question(chunk_text: str, candidate_qid: str | None) -> ProseFields | None:
    """Extract what a prose question states. None if it has no usable id."""
    lines = [ln.replace("\t", " ").strip() for ln in (chunk_text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return None

    first = lines[0]
    match = _LABEL_RE.match(first)
    qid = (match.group(1) if match else (candidate_qid or "")).strip()
    if not qid:
        return None

    question_text = _strip_label(first)
    # The wording may itself carry a bracketed hint, e.g. "[ENTER NUMBER 1-100]".
    inline_directive = _OPTION_TAG_RE.search(question_text)
    directives: list[str] = []
    if inline_directive:
        directives.append(inline_directive.group("tags").strip())
        question_text = _OPTION_TAG_RE.sub("", question_text).strip()

    instruction_text = question_text
    options: list[Option] = []
    terminate_codes: list[int] = []
    terminate_lines: list[str] = []
    randomize = False

    for line in lines[1:]:
        directive = _DIRECTIVE_LINE_RE.match(line)
        if directive:
            body = directive.group("body").strip()
            directives.append(body)
            if re.search(r"RANDOMI[SZ]E", body, re.I):
                randomize = True
            if _TERMINATE_TAG.search(body):
                # A document-level terminate rule ("CONTINUE IF AGES 18-60,
                # TERMINATE ALL OTHERS") is prose about a range, not per-option
                # codes — record it, do not invent an expression.
                terminate_lines.append(body)
            continue

        if _SECTION_ROUTING_RE.search(line):
            # Belongs to the next section's routing, not this option list.
            directives.append(line)
            continue

        if _INSTRUCTION_RE.match(line) and not options:
            instruction_text = f"{instruction_text} {line}"
            continue

        # Otherwise: an answer option. Codes are positional (1-based), which is
        # how prose lists are scripted.
        tags = _OPTION_TAG_RE.search(line)
        label = _OPTION_TAG_RE.sub("", line).strip() if tags else line
        if not label:
            continue
        code = len(options) + 1
        option = Option(code=code, label=label)
        if tags:
            tag_text = tags.group("tags")
            if _EXCLUSIVE_TAG.search(tag_text):
                option.exclusive = True
            if _ANCHOR_TAG.search(tag_text):
                option.anchored = True
            if _TERMINATE_TAG.search(tag_text):
                terminate_codes.append(code)
                terminate_lines.append(f"{label} [{tag_text.strip()}]")
        options.append(option)

    fields = ProseFields(
        qid=qid,
        text=question_text or None,
        options=options,
        randomize=randomize,
        directives=directives,
    )
    fields.qtype = _infer_type(instruction_text, options, directives)

    if terminate_codes:
        fields.terminate_if = Expression.from_dict(
            {
                "op": "in",
                "qid": qid,
                "value": sorted(terminate_codes),
                "verbatim_text": "; ".join(terminate_lines),
            }
        )
        fields.terminate_verbatim = "; ".join(terminate_lines)
    elif terminate_lines:
        # Stated in prose but not tied to option codes — preserve it and let a
        # model resolve it, never drop a screen-out.
        fields.terminate_verbatim = "; ".join(terminate_lines)
        fields.missing.append("terminate")

    if not fields.text:
        fields.missing.append("text")
    if fields.qtype is None:
        fields.missing.append("type")

    return fields
