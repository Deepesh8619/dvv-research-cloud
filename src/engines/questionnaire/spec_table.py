"""Read a labelled spec-table question from its STRUCTURE, without an LLM.

Many client questionnaires are written as one table per question with labelled
rows. The LABELS differ per client — one writes `Q Name`, another `Question ID`;
one writes `Among Who`, another `Base` or `Universe` — but the SHAPE is the
same: a label column, a value column, and numbered rows for the answer list.

Measured on one real 74-question document: every question carried labelled
id/type/base/show rows and 86% carried numbered option rows. We were paying
~6,100 tokens per question to have a language model read `Q Name | SS_RESP` out
of a cell, of which 65 tokens were the question and the rest was prompt and
schema overhead.

EXTENSIBILITY IS THE POINT. A new client convention is entries in
`FIELD_ALIASES`, never a code change here — the same contract profiles.py uses
for detection. Aliases are matched case- and punctuation-insensitively, so
`Q Name`, `q_name` and `Q NAME:` are one thing. Unrecognised labels are kept in
`extras` rather than dropped, so a convention nobody has taught us yet is
visible instead of silently lost.

Deliberately CONSERVATIVE: anything uncertain is left None and named in
`missing`, so the caller escalates to the LLM. A wrong deterministic answer is
worse than an expensive right one — it is silent and looks authoritative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.core.survey_definition import Expression, Option, QuestionType
from src.engines.questionnaire import option_directives
from src.engines.questionnaire.learned import (
    LearnedPattern,
    learned_field,
    learned_type,
)
from src.engines.questionnaire.prose_directives import is_single_select_wording
from src.intake.questionnaire_loader import DocumentBlock

# Row-label synonyms, by the field they populate. ADD A CLIENT'S WORDING HERE —
# that is the whole extension mechanism. Compared after _norm_label(), so case,
# punctuation and spacing do not matter.
FIELD_ALIASES: dict[str, set[str]] = {
    "qid": {
        "q name", "qname", "question id", "questionid", "qid", "variable",
        "variable name", "q no", "q number", "question name", "q",
    },
    "type": {
        "q type", "qtype", "question type", "type", "punch type", "punch",
        "answer type", "response type", "format",
    },
    "base": {
        "among who", "amongwho", "base", "universe", "asked of", "ask of",
        "who", "sample", "filter", "base definition",
    },
    "show": {
        "show hv", "show / hv", "show", "show if", "display logic", "skip logic",
        "routing", "logic", "condition",
    },
    "randomize": {
        "randomize", "randomise", "randomize response list", "rotation",
        "randomization", "order",
    },
    "notes": {
        "other details", "notes", "note", "instructions", "scripter notes",
        "programming notes", "programmer notes", "comments", "special instructions",
    },
    "text": {
        "question text", "question", "wording", "q text", "text",
    },
}

# Markers that introduce the answer list rather than being content.
_LIST_MARKERS = ("response list", "answer list", "options", "response options",
                 "code frame", "codes", "scale", "variable list", "statement list",
                 "item list", "row list")
# What is being rated, versus the scale it is rated on.
_ROW_LIST_MARKERS = ("variable list", "statement list", "item list", "row list")
# A scale labelled only at its ends: "10: Strongly Agree / 9: (blank) / ... /
# 1: Strongly Disagree". The blanks are real scale points — dropping them turned
# a ten-point scale into a two-point one.
_BLANK_CELL = re.compile(r"^\(\s*blank\s*\)$", re.I)


def _norm_label(value: str) -> str:
    """Comparable label: lowercase, no punctuation, single-spaced."""
    low = (value or "").strip().lower().rstrip(":")
    low = re.sub(r"[^a-z0-9\s]", " ", low)
    return " ".join(low.split())


def _field_for(label: str, patterns: list[LearnedPattern] | None = None) -> str | None:
    """Which field a row label populates, or None if we do not know it.

    Taught aliases are checked first. A client writing "Asked of:" instead of
    "Among who:" is the single commonest reason a spec table lands a real value
    in `extras` and then asks a model for something the document stated plainly
    — and it is exactly the correction a founder can make in one line.
    """
    taught = learned_field(patterns, label)
    if taught is not None:
        return taught
    norm = _norm_label(label)
    if not norm:
        return None
    for name, aliases in FIELD_ALIASES.items():
        if norm in aliases:
            return name
    return None


def _cells(row: list[str]) -> list[str]:
    """Row cells, trimmed, with merged-cell repetition collapsed.

    Word serialises a merged cell by repeating its value across every spanned
    column, so a two-column row arrives as five identical strings.
    """
    out: list[str] = []
    for raw in row:
        value = " ".join((raw or "").split())
        if not out or value != out[-1]:
            out.append(value)
    return out


# Blank template prompts printed in the form itself, not answers:
# "If not All, specify:", "If Yes, details:".
_TEMPLATE_PROMPT = re.compile(
    r"^if\s+.{0,24}?[,:]\s*(specify|details|describe)?\s*:?$", re.IGNORECASE
)


def _value_of(row: list[str]) -> str:
    for cell in _cells(row)[1:]:
        if cell and not _TEMPLATE_PROMPT.match(cell):
            return cell
    return ""


_CODE_RE = re.compile(r"^\d{1,3}$")

# Screen-out wording varies by client: TERMINATE / TERM / SCREEN OUT / SCREENOUT.
_TERMINATE_RE = re.compile(
    r"(?:terminate|term|screen\s*-?\s*out|screenout)\s*"
    # Hyphen included: "TERMINATE if 3-99 selected" is a RANGE of punch codes,
    # written constantly, and it parsed as nothing at all — so a screen-out the
    # document states plainly never fired. The instrument passed every other
    # check because a missing terminate looks exactly like a question that
    # screens nobody out.
    r"(?:if|when)?\s*(?:code[s]?\s*)?([\d,\-\u2013\s&and]+?)\s*"
    r"(not\s+selected|is\s+not|selected|chosen|$)",
    re.IGNORECASE,
)
# "TERMINATE if 13-99 selected at SS_REVENUE" — the screen-out is on ANOTHER
# question's answer. Naming this question instead would screen on the wrong one.
_TERMINATE_AT_RE = re.compile(r"\bat\s+([A-Za-z][A-Za-z0-9_]{1,30})\b", re.I)
# "3-99", "13 - 99": every punch from one bound to the other.
_CODE_RANGE_RE = re.compile(r"^\s*(\d{1,3})\s*[-\u2013]\s*(\d{1,3})\s*$")

# "[Numeric OE: 3-digit max]" in the response list: the respondent TYPES a
# number. A coded line beside it ("99: Prefer not to answer") is a way out of
# typing, not an answer list — read as one, the number box vanished and the
# question became a single choice with one option.
_NUMERIC_ENTRY_RE = re.compile(r"\[\s*numeric\b[^\]]*\]|\bnumeric\s+(?:OE|open)", re.I)
# "Allow 0-999 to be entered"
_ALLOWED_RANGE_RE = re.compile(r"\ballow\w*\s+(\d+)\s*(?:-|\u2013|to)\s*(\d+)", re.I)
# "3-digit max"
_DIGIT_MAX_RE = re.compile(r"(\d)\s*-?\s*digits?\s*max", re.I)
# "age<25", "age >= 65": a screen-out on the number typed.
_NUMERIC_BOUND_RE = re.compile(r"(?:^|[\s(,])[A-Za-z_]*\s*(<=|>=|<|>)\s*(\d+|\?\?+)")
# "OR if 99 selected"
_CODE_SELECTED_RE = re.compile(r"\b(\d{1,3})\s+(?:is\s+)?(?:selected|chosen)\b", re.I)

_TYPE_WORDS = [
    ("multi", QuestionType.MULTI),
    ("multiple", QuestionType.MULTI),
    ("checkbox", QuestionType.MULTI),
    ("single", QuestionType.SINGLE),
    ("radio", QuestionType.SINGLE),
    ("dropdown", QuestionType.DROPDOWN),
    ("numeric", QuestionType.NUMERIC),
    ("number", QuestionType.NUMERIC),
    ("open", QuestionType.OPEN_TEXT),
    ("verbatim", QuestionType.OPEN_TEXT),
    ("text", QuestionType.OPEN_TEXT),
    ("grid", QuestionType.GRID_SINGLE),
    ("matrix", QuestionType.GRID_SINGLE),
]


@dataclass
class SpecTableFields:
    """What the table itself states. `missing` is what still needs a model."""

    qid: str
    text: str | None = None
    qtype: QuestionType | None = None
    options: list[Option] = field(default_factory=list)
    # A GBK table states a grid as TWO lists: "Variable list" is what is being
    # rated, "Response list" is the scale it is rated on. Both were read into
    # `options`, so an 8-statement grid on a 10-point scale arrived as one flat
    # list of 18 answers with the codes colliding — which is what "duplicate
    # option code" and "empty options" have been reporting all along.
    rows: list[Option] = field(default_factory=list)
    cols: list[Option] = field(default_factory=list)
    terminate_if: Expression | None = None
    terminate_verbatim: str | None = None
    #: For a typed number: {"min": 0, "max": 999}. Options on a numeric question
    #: are its opt-out codes ("99: Prefer not to answer").
    numeric_range: dict[str, int] = field(default_factory=dict)
    base: str | None = None
    show: str | None = None
    randomize: str | None = None
    notes: str | None = None
    # Labels we did not recognise, kept so an unknown client convention is
    # visible (and teachable via the correction loop) rather than silently lost.
    extras: dict[str, str] = field(default_factory=dict)
    #: "[IF GOOGLE ADS CURRENT USER]" under answer 1: which answers carry a
    #: base of their own, and whether the wording named its source question.
    option_bases: list[dict] = field(default_factory=list)
    #: "[INSERT ... FROM DA_PLATFORM_USAGE 9-15]": the list continues with
    #: another question's codes. Read by the pass that fills lists.
    list_from: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    # field -> the exact row or fragment that decided it, so the review UI can
    # cite the source rather than asking a reviewer to trust the parse.
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """True when nothing needs a language model."""
        return not self.missing


def _parse_terminate(
    notes: str, options: list[Option], qid: str
) -> Expression | None:
    """Turn 'TERMINATE if 5,6 selected' into a real Expression.

    None on anything unreadable: a mis-parsed terminate screens out the wrong
    respondents, which costs real money in field, so silence is the right
    failure mode.
    """
    match = _TERMINATE_RE.search(notes or "")
    if not match:
        return None
    raw = match.group(1) or ""
    known = {o.code for o in options}

    # A screen-out can sit on another question's answer: "TERMINATE if 13-99
    # selected at SS_REVENUE", written in the notes of the question that comes
    # after it. Resolve the target FIRST, because it decides whose punch codes a
    # range should be read against.
    named = _TERMINATE_AT_RE.search(notes or "")
    other_question = bool(named and named.group(1).upper() != qid.upper())

    band = _CODE_RANGE_RE.match(raw)
    if band:
        low, high = int(band.group(1)), int(band.group(2))
        if low > high:
            return None
        if other_question:
            # The options in hand belong to the WRONG question. Narrowing "13-99"
            # against them produced a screen-out on a single code — a rule that
            # fires for almost nobody, which is the quietest way to get this
            # wrong. Keep the span the document wrote; codes the target question
            # does not have simply never match.
            codes = list(range(low, high + 1))
        else:
            # Only codes this question actually offers. "3-99" is the scripter's
            # shorthand for "everything from 3 up".
            codes = sorted(c for c in known if low <= c <= high)
        if not codes:
            return None
    else:
        codes = [int(c) for c in re.findall(r"\d+", raw)]
    if not codes:
        return None
    negated = "not" in (match.group(2) or "").lower()

    if known and not other_question and not set(codes).issubset(known):
        return None  # refers to codes this question does not have — do not guess

    if other_question:
        qid = named.group(1)

    # The condition is on THIS question's own answer ("TERMINATE if 5,6
    # selected" means codes of this question), so it must name this qid. A
    # placeholder like "__self__" is not a real qid, and the cross-chunk
    # resolver in extract_survey_definition nulls any expression referencing a
    # qid that exists nowhere — which silently discarded every terminate this
    # parser extracted. Caught only by running a full extraction end to end.
    # `values`, not `value`: `in` is defined as membership in `values`, and
    # both evaluate() and the SurveyJS compiler read that field. Codes in
    # `value` meant the screen-out matched nobody.
    verbatim = (notes or "").strip()
    membership = {
        "op": "in",
        "qid": qid,
        "values": codes,
        "verbatim_text": verbatim,
    }
    if not negated:
        return Expression.from_dict(membership)
    # "TERMINATE if 1 not selected" is the inverse. There is no `not_in` op —
    # emitting one produced an expression the evaluator, the compiler and the
    # review UI all failed to understand. `not` over `in` is the declared way to
    # say this and is already handled everywhere.
    return Expression.from_dict(
        {"op": "not", "children": [membership], "verbatim_text": verbatim}
    )


# An explicit request for typed input, used to tell an open end apart from a
# display screen when neither has a response list.
_OPEN_ENTRY_RE = re.compile(
    r"\bopen[- ]?end|\btextbox\b|\bOE\b|specify|please describe|"
    r"in your own words|enter|type|_\s*_\s*_",
    re.I,
)


def _infer_type(
    stated: str,
    options: list[Option],
    text: str | None,
    patterns: list[LearnedPattern] | None = None,
) -> tuple[QuestionType | None, str]:
    """Resolve the question type and the fragment that decided it.

    Scripters frequently leave the cell blank, so the answer often comes from
    the wording instead — the reviewer needs to be told which.
    """
    taught = learned_type(patterns, stated or "", text or "")
    if taught is not None:
        return taught
    low = (stated or "").strip().lower()
    for word, qtype in _TYPE_WORDS:
        if word in low:
            return qtype, f"Q type: {stated.strip()}"
    if low:
        # Said something unrecognised — ask rather than assume.
        return None, ""
    if not options:
        if not text:
            return None, ""
        # No response list is NOT automatically an open end. Intro and
        # instruction screens have no options either, and typing them open_text
        # puts a text box in front of a respondent who was only being greeted
        # ("Thank you for taking the time to participate!"). A question that
        # genuinely wants typing either asks something or says so in a
        # directive; display text does neither.
        asks_something = "?" in text
        wants_typing = _OPEN_ENTRY_RE.search(text) is not None
        if asks_something or wants_typing:
            return QuestionType.OPEN_TEXT, "a question with no response list"
        return QuestionType.INFO, "display text — no question asked, no options"
    if is_single_select_wording(text or ""):
        # The type cell is blank but the wording settles it: a superlative or a
        # single-fact question cannot be multi-select. This is the commonest
        # remaining gap in spec-table documents, where "Q type: (blank)" is
        # routine and the wording is the only statement of intent.
        return QuestionType.SINGLE, "wording that admits only one answer"
    # Has options but no stated type: single vs multi changes the data
    # structure, so this is a real question, not a guess.
    return None, ""


def _numeric_range(text: str) -> dict[str, int]:
    """The range a typed number may take, when the document states one."""
    allowed = _ALLOWED_RANGE_RE.search(text)
    if allowed:
        low, high = int(allowed.group(1)), int(allowed.group(2))
        if low <= high:
            return {"min": low, "max": high}
    digits = _DIGIT_MAX_RE.search(text)
    if digits:
        return {"min": 0, "max": 10 ** int(digits.group(1)) - 1}
    return {}


def _parse_numeric_terminate(
    notes: str, qid: str, opt_outs: list[Option]
) -> tuple[Expression | None, bool]:
    """ "TERMINATE if age<25 or age>?? OR if 99 selected" on a typed number.

    Returns the screen-out it can state exactly, and whether part of it could
    NOT be read. A bound written "??" is a number the scripter never filled in:
    the rest still holds (under 25 is out whatever the upper limit), and the
    gap goes to a person rather than being guessed.
    """
    if not re.search(r"terminate|term\b|screen\s*-?\s*out", notes or "", re.I):
        return None, False
    rule = re.split(r"terminate|term\b|screen\s*-?\s*out", notes, maxsplit=1, flags=re.I)[1]
    parts: list[Expression] = []
    unread = False
    for op, bound in _NUMERIC_BOUND_RE.findall(rule):
        if bound.startswith("?"):
            unread = True
            continue
        value = int(bound)
        # Whole numbers, so "<= 24" is "< 25"; the model has no <= of its own.
        if op == "<=":
            op, value = "<", value + 1
        elif op == ">=":
            op, value = ">", value - 1
        parts.append(Expression(op="lt" if op == "<" else "gt", qid=qid, value=value))
    known = {o.code for o in opt_outs}
    codes = [int(c) for c in _CODE_SELECTED_RE.findall(rule) if int(c) in known]
    if codes:
        parts.append(Expression(op="in", qid=qid, values=codes))
    if not parts:
        return None, True
    expression = parts[0] if len(parts) == 1 else Expression(op="or", children=parts)
    expression.verbatim_text = notes.strip()
    return expression, unread


def parse_spec_table(
    block: DocumentBlock, patterns: list[LearnedPattern] | None = None
) -> SpecTableFields | None:
    """Extract everything a labelled spec table states. None if it is not one."""
    rows = block.rows or []
    if not rows:
        return None

    found: dict[str, str] = {}
    extras: dict[str, str] = {}
    options: list[Option] = []
    row_items: list[Option] = []
    unlabelled: list[str] = []
    #: The answer a bracketed directive would belong to, and what was found.
    last_option: Option | None = None
    option_bases: list[dict] = []
    list_sources_found: list[dict] = []
    seen_qid_row = False
    in_row_list = False
    in_list = False

    table_text = "\n".join(" ".join(_cells(row)) for row in rows)
    for row in rows:
        cells = _cells(row)

        # A marker line switches which list the codes below it belong to, and it
        # is written with an EMPTY first cell — the label sits in the merged
        # columns beside it — so it has to be read BEFORE the blank-code skip
        # below, or the switch never happens and both lists pour into one.
        marker = next((c.strip().lower() for c in cells if c.strip()), "")
        if any(marker.startswith(m) for m in _LIST_MARKERS):
            in_row_list = any(marker.startswith(m) for m in _ROW_LIST_MARKERS)
            in_list = True

        if not cells or not cells[0]:
            continue

        if _CODE_RE.match(cells[0]):
            value = _value_of(row)
            if in_list and (not value or _BLANK_CELL.match(value)):
                # A ten-point scale is labelled at its ends and left blank
                # between: "10: Strongly Agree / 9: / ... / 1: Strongly
                # Disagree". Those middle points are real answers — dropping
                # them turned a ten-point scale into a two-point one, and every
                # respondent who picked a 7 had nowhere to land. The number is
                # what they see, so the number is the label.
                value = cells[0].strip()
            if value:
                target = row_items if in_row_list else options
                target.append(Option(code=int(cells[0]), label=value))
                last_option = target[-1]
            continue

        name = _field_for(cells[0], patterns)
        if name:
            value = _value_of(row)
            found.setdefault(name, value)
            if name == "qid":
                seen_qid_row = True
            continue

        if len(cells) > 1 and _value_of(row):
            # A labelled row whose label we do not recognise yet.
            extras[cells[0]] = _value_of(row)
        elif seen_qid_row and len(cells) == 1:
            # A bracketed line UNDER an answer belongs to that answer:
            # "1: Google Ads" then "[IF GOOGLE ADS CURRENT USER]". Read as an
            # answer of its own it became a punch code in the data file and a
            # choice on the respondent's screen.
            directive = option_directives.read(cells[0])
            if directive and last_option is not None and directive["kind"] == "base":
                option_directives.attach(last_option, cells[0])
                option_bases.append({"code": last_option.code, "verbatim": directive["verbatim"],
                                     "resolved": last_option.show_if is not None})
                continue
            if directive and directive["kind"] == "list_from":
                list_sources_found.append(directive)
                continue
            low = cells[0].lower()
            if not any(low.startswith(m) for m in _LIST_MARKERS):
                unlabelled.append(cells[0])

    qid = (found.get("qid") or "").strip()
    if not qid:
        return None

    # Prefer an explicit question-text row; otherwise the first unlabelled line
    # after the id row, which is where the wording sits in every convention
    # seen so far.
    text = (found.get("text") or "").strip() or (unlabelled[0] if unlabelled else None)
    notes = found.get("notes", "")

    # Two lists means a grid: the variable list is rated ON the response list.
    # One list is an ordinary answer list, exactly as before.
    grid_rows, grid_cols = (row_items, options) if row_items and options else ([], [])

    fields = SpecTableFields(
        qid=qid,
        text=text or None,
        options=[] if grid_rows else options,
        rows=grid_rows,
        cols=grid_cols,
        base=found.get("base") or None,
        show=found.get("show") or None,
        randomize=found.get("randomize") or None,
        notes=notes or None,
        extras=extras,
        option_bases=option_bases,
        list_from=list_sources_found,
    )
    fields.qtype, type_evidence = _infer_type(
        found.get("type", ""), options, text, patterns
    )
    if type_evidence:
        fields.evidence["type"] = type_evidence
    if grid_rows:
        # The shape settles the type: each variable is answered on the response
        # scale, which is a grid however the "Q type" cell was filled in.
        fields.qtype = QuestionType.GRID_SINGLE
        fields.evidence["type"] = (
            f"a variable list of {len(grid_rows)} rated on a "
            f"{len(grid_cols)}-point response list"
        )
    numeric_entry = _NUMERIC_ENTRY_RE.search(table_text)
    stated_type = (found.get("type") or "").strip()
    if numeric_entry and not grid_rows and (
        not stated_type or _BLANK_CELL.match(stated_type) or "numer" in stated_type.lower()
    ):
        fields.qtype = QuestionType.NUMERIC
        fields.evidence["type"] = numeric_entry.group(0)
        fields.numeric_range = _numeric_range(table_text)
        if fields.numeric_range:
            fields.evidence["range"] = (
                f"{fields.numeric_range['min']}-{fields.numeric_range['max']}"
            )
        # The coded lines are ways out of typing a number.
        for option in fields.options:
            option.exclusive = True
            option.anchored = True
        clash = [
            o.code for o in fields.options
            if fields.numeric_range
            and fields.numeric_range["min"] <= o.code <= fields.numeric_range["max"]
        ]
        if clash:
            # 99 typed as an age and 99 meaning "prefer not to say" land in the
            # same column as the same number. The document chose it; say so.
            fields.evidence["opt_out"] = (
                f"code {clash[0]} is also a number a respondent may type "
                f"({fields.evidence.get('range')}) — the two cannot be told apart in the data"
            )
        fields.terminate_if, unread = _parse_numeric_terminate(notes, qid, fields.options)
        if unread:
            # Not sent to a model: it cannot know a number the scripter never
            # wrote either, and it would overwrite the part that WAS exact.
            fields.evidence["terminate_gap"] = (
                "part of this screen-out is not stated (a bound written '??') — "
                "the rest is applied; confirm the missing limit"
            )
    else:
        fields.terminate_if = _parse_terminate(notes, options, qid)
    if fields.terminate_if is not None:
        fields.terminate_verbatim = notes.strip()
        fields.evidence["terminate"] = notes.strip()
    if fields.base:
        fields.evidence["base"] = f"Among who: {fields.base}"

    if not fields.text:
        fields.missing.append("text")
    if fields.qtype is None:
        fields.missing.append("type")
    # A base in prose ("those aware of the brand") needs judgment; a blank base
    # means everyone and needs none; a coded one ("S1=1-3") is already usable.
    if fields.base and not re.fullmatch(r"[\w\s=,<>\-\d/]+", fields.base):
        fields.missing.append("base")
    if notes and re.search(r"terminate|term\b|screen\s*-?\s*out", notes, re.IGNORECASE):
        if fields.terminate_if is None and "terminate_gap" not in fields.evidence:
            # It states a screen-out we could not parse — never silently drop it.
            fields.missing.append("terminate")

    return fields
