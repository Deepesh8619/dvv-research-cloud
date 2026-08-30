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
# Built from an inventory of EVERY bracketed directive across the three real
# client documents — 116 distinct shapes. Numeric forms are checked before the
# open-text ones, because "NUMERIC OE" and "OE" both contain "OE".
_TYPE_PATTERNS: list[tuple[re.Pattern, QuestionType]] = [
    (re.compile(r"select all that apply|choose all that apply|multiple answers",
                re.I), QuestionType.MULTI),
    (re.compile(r"select one|choose one|single answer|select your", re.I),
     QuestionType.SINGLE),
    # NUMERIC first: "NUMERIC OE: 3-DIGIT MINIMUM" is a number, not a verbatim.
    (re.compile(r"numeric\s*oe|record number|enter (a )?number|\bnumeric\b|"
                r"\byears\b|\bage\b.*\bnumber\b|number\s+\d+\s*-\s*\d+|"
                r"\d+\s*-\s*digit", re.I),
     QuestionType.NUMERIC),
    (re.compile(r"\brank\b.{0,20}\b(top|first|three|order)\b|\brank \d[, \d]*and\b|"
                r"^\s*rank\b", re.I), QuestionType.RANK),
    # TEXTBOX (11x) and "OE: n-CHARACTER MINIMUM" (6x) are the commonest
    # open-text declarations in these documents and were both being missed.
    (re.compile(r"_\s*_\s*_|open[- ]?end|\btextbox\b|\boe\b|please describe|"
                r"in your own words|type your|enter response|verbatim", re.I),
     QuestionType.OPEN_TEXT),
]

# The option list is not written out here — it is taken from another question
# ("[SHOW Q26 LIST. RANDOMIZE]", "[INSERT RESPONSES FROM Q4]"). The type
# instruction is still stated and still trustworthy; only the options are absent.
_BORROWED_OPTIONS_RE = re.compile(
    r"\bSHOW\s+[A-Z]{0,3}\d+[A-Z]?\s+LIST\b|"
    r"\bINSERT\s+.{0,40}?\bFROM\s+[A-Z]{0,3}\d+", re.I
)

# A directive naming ROWS or STATEMENTS is describing a grid: the option list
# is the row set, not the answer set. ("SHOW ONLY ITEMS SELECTED AT Q18 IN
# ROWS", "RANDOMIZE STATEMENTS".) Unlike "each of the following", which is
# suggestive prose, this is the scripter stating the structure outright.
_GRID_DIRECTIVE_RE = re.compile(r"\bIN ROWS\b|\bROWS\b|\bSTATEMENTS\b", re.I)

# Tags carried by an individual option.
_TERMINATE_TAG = re.compile(r"\bTERMINATE\b|\bSCREEN\s*-?\s*OUT\b", re.I)
_EXCLUSIVE_TAG = re.compile(r"\bEXCLUSIVE\b", re.I)
_ANCHOR_TAG = re.compile(r"\bANCHOR\b", re.I)

# A scale row: "0=Not Important At All | 1 | 2 | ... | 10=Very Important".
# Pipe-separated points above a statement list is how these documents write a
# rating grid; the statements below are the rows, not the answer options.
_SCALE_ROW_RE = re.compile(r"^[^|]+(\|[^|]+){3,}$")

# A wording that opens with an all-caps CODE/RECODE instruction is addressed to
# the scripter, not the respondent: "CODE AGE - BASED ON RESPONSES AT S1...".
# These are derived variables computed from other answers, so they have no type
# to find and must not be counted as a question the model still has to resolve.
_DERIVED_RE = re.compile(r"^(RE)?CODE\b")


# Wordings that cannot be multi-select whatever the scripter left in the type
# cell. A superlative ("best describes") admits exactly one answer by
# construction, and "what is your <fact>" asks for one value. Deliberately does
# NOT include "how often" or "how would you rate": those are commonly grids in
# these documents, and guessing single there would be silently wrong.
_SINGLE_WORDING_RE = re.compile(
    r"\bbest (describes|represents|matches|fits|reflects)\b|"
    r"\bwhich one\b|\bhow involved are you\b|\bwhat is your\b",
    re.I,
)


def is_single_select_wording(text: str) -> bool:
    """True when the wording itself rules out a multi-select answer."""
    return bool(_SINGLE_WORDING_RE.search(text or ""))


def is_derived_wording(text: str) -> bool:
    """All-caps CODE/RECODE wording = an instruction to the scripter.

    The "no lowercase" test is what keeps this from swallowing a real question
    ("Code the item you bought"): these documents shout scripter instructions
    and speak to respondents in sentence case. Punctuation varies (en dashes,
    ampersands, brackets), so testing case is more portable than enumerating
    the allowed characters — which is how the first version missed most of them.
    """
    stripped = text.strip()
    return bool(_DERIVED_RE.match(stripped)) and not any(c.islower() for c in stripped)

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
    # A scripter-computed variable ("CODE AGE - BASED ON S1..."), not a question.
    derived: bool = False
    # Directives we recognised but do not model yet — kept visible, never
    # silently dropped, so an unhandled convention is discoverable.
    directives: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing


def _strip_label(line: str) -> str:
    return _LABEL_RE.sub("", line, count=1).strip()


def _is_binary_choice(options: list[Option]) -> bool:
    """Exactly Yes/No — the one option shape whose type is not a judgement call.

    `_infer_type` deliberately refuses to guess single vs multi from options
    alone, because that choice changes the stored data structure. A two-option
    yes/no list is the documented exception: it cannot be multi-select in any
    questionnaire convention, and it is the commonest shape in these documents'
    screener sections.
    """
    if len(options) != 2:
        return False
    labels = {re.sub(r"[^a-z]", "", o.label.lower()) for o in options}
    return labels in ({"yes", "no"}, {"yesnoperm"}) or labels == {"no", "yes"}


def _infer_type(
    text: str,
    options: list[Option],
    directives: list[str] | None = None,
    borrowed_options: bool = False,
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
                if (
                    qtype in (QuestionType.MULTI, QuestionType.SINGLE)
                    and not options
                    and not borrowed_options
                ):
                    continue
                return qtype
    for pattern, qtype in _TYPE_PATTERNS:
        if pattern.search(text):
            # "Select all that apply" with no options is a contradiction; trust
            # the instruction only when the shape agrees with it.
            if (
                qtype in (QuestionType.MULTI, QuestionType.SINGLE)
                and not options
                and not borrowed_options
            ):
                continue
            return qtype
    if _is_binary_choice(options) or (options and is_single_select_wording(text)):
        return QuestionType.SINGLE
    # Options but no instruction: single vs multi changes the data structure,
    # so this is a real question for a model, not a guess.
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
    scale_row: str | None = None

    for line in lines[1:]:
        directive = _DIRECTIVE_LINE_RE.match(line)
        if directive:
            body = directive.group("body").strip()
            directives.append(body)
            if re.search(r"RANDOMI[SZ]E|\bROTATE\b", body, re.I) and not re.search(
                r"KEEP ORDER|DO NOT RANDOMI[SZ]E", body, re.I
            ):
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

        if _SCALE_ROW_RE.match(line):
            # The scale points define a rating grid; the lines after it are the
            # statements being rated. Record the scale, do not treat the points
            # as this question's answer options.
            directives.append(line)
            scale_row = line
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
    if question_text and is_derived_wording(question_text):
        # Scripter-computed, not asked. Labelled the same way the extractor
        # labels a HID_ variable, so the review UI says "needs manual setup".
        fields.derived = True
        fields.qtype = QuestionType.INFO
    elif scale_row or (
        options and any(_GRID_DIRECTIVE_RE.search(d) for d in directives)
    ):
        fields.qtype = QuestionType.GRID_SINGLE
    else:
        fields.qtype = _infer_type(
            instruction_text,
            options,
            directives,
            borrowed_options=any(_BORROWED_OPTIONS_RE.search(d) for d in directives),
        )

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
