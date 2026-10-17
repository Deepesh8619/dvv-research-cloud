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
from src.engines.questionnaire.flag_reasons import auto_answer_lines
from src.engines.questionnaire.learned import (
    LearnedPattern,
    learned_field,
    learned_type,
)
from src.engines.questionnaire.prose_directives import is_single_select_wording
from src.engines.questionnaire.routing_prose import (
    _IN_LIST,
    _letters_to_codes,
)
from src.engines.questionnaire.routing_prose import _condition as _routing_parse
from src.intake.questionnaire_loader import DocumentBlock

# Row-label synonyms, by the field they populate. ADD A CLIENT'S WORDING HERE —
# that is the whole extension mechanism. Compared after _norm_label(), so case,
# punctuation and spacing do not matter.
FIELD_ALIASES: dict[str, set[str]] = {
    "qid": {
        "q name", "qname", "question id", "questionid", "qid", "variable",
        "variable name", "q no", "q number", "question name", "q", "variable id",
        "var name", "varname", "question no", "question number", "var",
    },
    "type": {
        "q type", "qtype", "question type", "type", "punch type", "punch",
        "answer type", "response type", "format", "question format", "item type",
    },
    "base": {
        "among who", "amongwho", "base", "universe", "asked of", "ask of",
        "who", "sample", "filter", "base definition", "asked to", "who is asked",
        "respondent base", "respondents", "audience", "target", "ask if",
        "show only if", "only if", "universe base", "population",
    },
    "show": {
        "show hv", "show / hv", "show", "show if", "display logic", "skip logic",
        "routing", "logic", "condition", "hv", "show hidden", "visibility",
        "display", "show to",
    },
    "randomize": {
        "randomize", "randomise", "randomize response list", "rotation",
        "randomization", "order",
        # A grid's statements ARE its variable list: shuffling them is the
        # grid's randomisation (its scale, the response list, never moves).
        "randomize variable list", "randomise variable list", "randomize statement list",
        "randomize rows",
    },
    "notes": {
        "other details", "notes", "note", "instructions", "scripter notes",
        "programming notes", "programmer notes", "comments", "special instructions",
        "scripter instructions", "scripter instruction", "scripting notes",
        "script notes", "programming instructions", "programmer instructions",
        "pn", "p n", "programming note", "programmer note", "scripting instructions",
        "validation", "restrictions",
    },
    "text": {
        "question text", "question", "wording", "q text", "text", "question wording",
        "stem", "question stem", "q wording", "prompt", "screen text", "text to show",
    },
}

# Markers that introduce the answer list rather than being content.
_LIST_MARKERS = ("response list", "answer list", "options", "response options",
                 "code frame", "codes", "scale", "variable list", "statement list",
                 "item list", "row list", "answers", "answer options", "rows", "columns",
                 "statements", "items")
# What is being rated, versus the scale it is rated on.
_ROW_LIST_MARKERS = ("variable list", "statement list", "item list", "row list", "rows",
                      "statements", "items")
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


#: "Other >>>" / "Custom >>>": the value is in the NEXT cell, after its prompt —
#: "Among who | Other >>> | If not All, specify: C_ACCOUNT = 2". Reading the
#: first cell kept "Other >>>", so every base in such a table was lost.
_SEE_NEXT = re.compile(r">{2,}\s*$")
_PROMPT_PREFIX = re.compile(r"^if\s+.{0,24}?[,:]\s*(?:specify|details|describe)?\s*:?\s*", re.I)


def _value_of(row: list[str]) -> str:
    cells = _cells(row)[1:]
    for i, cell in enumerate(cells):
        if not cell or _TEMPLATE_PROMPT.match(cell):
            continue
        if _SEE_NEXT.search(cell):
            for later in cells[i + 1:]:
                stated = _PROMPT_PREFIX.sub("", later).strip()
                if stated:
                    return stated
        return cell
    return ""



#: A whole answer list written in ONE cell, not one row per answer:
#:     "1: Yes   2: No   99: Don't know"      (spaces between items)
#:     "1 = Yes // 2 = No"                    (// between items)
#:     "1 = Yes⏎2 = No"                       (a line per item)
_INLINE_SPLIT = re.compile(r"\s*//\s*|[\r\n]+|\s{2,}(?=\[?\d{1,3}\]?\s*[:=.)]\s*\S)")
_INLINE_ITEM = re.compile(r"^\[?(\d{1,3})\]?\s*[:=.)]\s*(\S.*)$")
#: "SMB (10-99) = SS_EMPLOYEES = 3": the label, then how a hidden variable derives it.
_DERIVATION = re.compile(r"\s+=\s+(?=[A-Za-z][A-Za-z0-9_]*\s*(?:=|in\b))")
_TAG_WORDS = (r"anchor(?:ed)?|exclusive|fix(?:ed)?(?:\s+position)?|single|"
              r"screen\s*-?\s*out|terminate|close")
_TRAILING_TAG = re.compile(
    rf"\s*[\[(]\s*((?:{_TAG_WORDS})(?:\s*[,/&+]\s*(?:{_TAG_WORDS}))*)\s*[\])]\s*$", re.I
)


def _split_option_tags(label: str) -> tuple[str, dict[str, bool]]:
    """"None of these [ANCHOR] (exclusive)" -> ("None of these", flags).

    Only the scripter words are taken. "(please specify)" and "(10-99)" are part
    of what the respondent reads and stay in the label.
    """
    flags: dict[str, bool] = {}
    while (m := _TRAILING_TAG.search(label)):
        tag = m.group(1).lower()
        if "anchor" in tag or "fix" in tag:
            flags["anchored"] = True
        if "exclusive" in tag or "single" in tag:
            flags["exclusive"] = True
        if "screen" in tag or "terminate" in tag or "close" in tag:
            flags["terminate"] = True
        label = label[: m.start()].rstrip()
    return label, flags


def _inline_items(row: list[str], minimum: int = 2) -> list[tuple[int, str, dict[str, bool], str | None]] | None:
    """(code, label, tags, derivation) per answer of a one-cell list, else None.

    All-or-nothing: one piece that is not a coded answer means this is a sentence
    that happens to contain digits, and reading part of it would invent answers.
    """
    cells = [c for c in (row or [])[1:] if (c or "").strip()]
    if not cells:
        return None
    pieces = [p.strip() for p in _INLINE_SPLIT.split(cells[0]) if p and p.strip()]
    if len(pieces) < minimum:
        return None
    items = []
    for piece in pieces:
        m = _INLINE_ITEM.match(piece)
        if not m:
            return None
        label, tags = _split_option_tags(m.group(2).strip())
        derivation = None
        if (d := _DERIVATION.search(label)):
            label, derivation = label[: d.start()].rstrip(), label[d.end():].strip()
        if not label:
            return None
        items.append((int(m.group(1)), label, tags, derivation))
    if len({i[0] for i in items}) != len(items):
        return None
    return items

_ALL_RESPONDENTS = re.compile(r"^\s*(?:all(?:\s+respondents)?|everyone|total)\s*\.?\s*$", re.I)
#: "C_ACCOUNT = 2", "HID_GOOGLE_USER = 2 or 3", "DA_PLACEMENT = 3 and/or 4", "S1=1-3".
_CODED_BASE = re.compile(
    r"\b(?P<qid>[A-Za-z][A-Za-z0-9_]{1,40})\s*=\s*"
    r"(?P<codes>\d{1,3}(?:\s*(?:-|–|,|or|and/or)\s*\d{1,3})*)",
)
#: Wording that means more than one coded condition — left for a person/model.
_COMPOUND = re.compile(r"\band\b(?!/or)|\bfor at least\b|\bselected at\b|\bonly show\b", re.I)


def base_condition(base: str | None) -> Expression | None:
    """A show condition from a base that states exactly one coded condition.

    "Google Ads Current User (HID_GOOGLE_USER = 1)" -> HID_GOOGLE_USER in [1].
    Anything with a second condition ("... AND 1-98 selected at X") or none at
    all ("Provider aware based on HID_AWARE") returns None.
    """
    found = list(_CODED_BASE.finditer(base or ""))
    if len(found) != 1:
        return None
    rest = (base[: found[0].start()] + base[found[0].end():])
    if _COMPOUND.search(rest) or _COMPOUND.search(found[0].group("codes")):
        return None
    values: list[int] = []
    for part in re.split(r"\s*(?:,|or|and/or)\s*", found[0].group("codes")):
        lo, _, hi = part.replace("–", "-").partition("-")
        low, high = int(lo), int(hi or lo)
        if high < low:
            return None
        values += range(low, high + 1)
    return Expression(op="in", qid=found[0].group("qid"), values=values,
                      verbatim_text=base.strip())


_BASE_PREAMBLE = re.compile(
    r"^\s*(?:only\s+)?(?:ask\s+|show\s+|display\s+)?(?:(?:those|respondents?)\s+)?"
    r"(?:where|who|if)\b\s*[:\-]?\s*", re.I)


def _routing_condition(base: str | None) -> Expression | None:
    """A base written as a plain test: "IF SVOD_AWARE in (1-6)", "Q3 in (1,3)",
    "Those where SW_CONSIDER = 1 or 2", "HH_SIZE >= 2".

    The whole text must be the condition: routing_prose reads it exactly or not
    at all, so "aware of X (S2 = 1) and 1-98 at Q7" is still left to a person.
    """
    text = _BASE_PREAMBLE.sub("", (base or "").strip()).rstrip(".")
    if not text:
        return None
    found = _routing_parse(_IN_LIST.sub(r" = \1", _letters_to_codes(text)))
    if found is not None:
        found.verbatim_text = base.strip()
    return found


#: The header of the column a hidden variable states its derivation in.
_DEFINITION_HEADER = re.compile(r"^\s*definitions?\s*:?\s*$", re.I)


def _definition_column(rows: list[list[str]]) -> int | None:
    """Which column holds "Definition:", if this table has one.

    A hidden variable's spec table carries `Label: | Definition:` over its
    codes, and the definition cell is the derivation — "HID_GEO_DIVISION =
    1,2". Found by its header rather than by position, because the label
    occupies a merged cell and the column index differs per table.
    """
    for row in rows:
        found = [i for i, cell in enumerate(row) if _DEFINITION_HEADER.match(cell or "")]
        if found:
            return max(found)
    return None


def _definition_in(row: list[str], column: int | None) -> str | None:
    """The derivation this option row states, or None.

    Never the label: a merged label cell repeats across columns, so a row whose
    definition column holds the same text as its label is a table with no
    definitions at all, and reading it would give every option a "derivation"
    that is just its own name.
    """
    if column is None or column >= len(row):
        return None
    value = (row[column] or "").strip()
    if not value or _TEMPLATE_PROMPT.match(value):
        return None
    if any(value == (cell or "").strip() for cell in row[1:column]):
        return None
    return value


_CODE_RE = re.compile(r"^\d{1,3}$")
#: Programming instructions that turn up in a cell labelled "Logic" / "Show".
_SCRIPTER_DIRECTIVE = re.compile(
    r"\brandomi[sz]e|\brotate\b|\bmax(?:imum)?\s+\d|\ballow\w*\s+(?:range|\d)|\brange\b|"
    r"\bmust\s+(?:total|sum)|\btotal\b|\bexclusive\b|\banchor|\bdisplay\s+only\b|"
    r"\bkeep\b|\brank\s+(?:top\s+)?\d",
    re.I,
)
_RANDOMIZE_WORD = re.compile(r"\brandomi[sz]e|\brotate\b", re.I)

# Screen-out wording varies by client: TERMINATE / TERM / SCREEN OUT / SCREENOUT.
_TERMINATE_RE = re.compile(
    r"(?:terminate|term|screen\s*-?\s*out|screenout)\s*"
    # Hyphen included: "TERMINATE if 3-99 selected" is a RANGE of punch codes,
    # written constantly, and it parsed as nothing at all — so a screen-out the
    # document states plainly never fired. The instrument passed every other
    # check because a missing terminate looks exactly like a question that
    # screens nobody out.
    # "TERMINATE if selects punch 1, 10, or 99": the verb and "punch" are
    # filler, and "or" joins codes like a comma does.
    r"(?:if|when)?\s*(?:selects?\s+)?(?:punch(?:es)?\s+|code[s]?\s*)?"
    # "TERMINATE if AGE_BAND = 1, 8": the question and an equals sign, then codes.
    r"(?:(?P<on>[A-Za-z][A-Za-z0-9_]{1,40})\s*(?:=|==|\bin\b|\bis\b)\s*)?"
    r"([\d,\-\u2013\s&andor]+?)\s*"
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
_ALLOWED_RANGE_RE = re.compile(
    r"\b(?:allow\w*|range|between)(?:\s+range)?(?:\s+of)?\s*[:=]?\s*"
    r"(-?\d[\d,]*)\s*(?:-|\u2013|to)\s*(\d[\d,]*)", re.I)
# "3-digit max"
_DIGIT_MAX_RE = re.compile(r"(\d)\s*-?\s*digits?\s*max", re.I)
# "age<25", "age >= 65": a screen-out on the number typed.
_NUMERIC_BOUND_RE = re.compile(r"(?:^|[\s(,])[A-Za-z_]*\s*(<=|>=|<|>)\s*(\d+|\?\?+)")
# "OR if 99 selected"
_CODE_SELECTED_RE = re.compile(r"\b(\d{1,3})\s+(?:is\s+)?(?:selected|chosen)\b", re.I)

# "Multi-line (large) Text OE", "Single-line Text OE". These say how BIG the
# text box is, not how many answers there are — and matching "multi" or
# "single" as a substring read the box size as the response type. Both are open
# ends, and both arrived as a choice question with no options: a question a
# respondent cannot answer and a data column that will never be filled. Removed
# before the type words are read, so "Text OE" is what decides it. A response
# type is never written "multi-line": "Std Multi-Response (MR)" is untouched.
_LAYOUT_QUALIFIER = re.compile(r"\b(?:multi|single|one)[\s-]*line\b", re.I)

# "Text Only", "Display text", "No response" — a screen the respondent READS.
# A transition ("In this next section, we'll focus on two platforms...") is not
# a question, and matching the bare word "text" typed it as an open end: the
# respondent was shown a paragraph and a box to type into, and the data file
# carried a column for an answer nobody was asked for. Checked before the type
# words, because "Text Only" contains "text" and would otherwise win.
_DISPLAY_ONLY = re.compile(
    r"\btext\s*only\b|\bdisplay\s+(?:text|only)\b|\bno\s+response\b|"
    r"\binfo(?:rmation)?\b|\bintro(?:duction)?\b|\btransition\b|"
    r"\bderived\b|\bcomputed\b|\bnot\s+shown\b|\bhidden\b",
    re.I,
)

#: A type that says the answers are numbers typed per item: "Constant sum
#: (numeric per row)", "Number entry per statement (sum)".
_SUM_BOXES = re.compile(r"constant\s*sum|\b(?:numeric|number)\b.*\bper\b|\bsum\b", re.I)
#: "Sum must be 100", "Rows must total 100", "Total = 100%".
_STATED_SUM = re.compile(r"\b(?:sum|total|add(?:s)?\s+up)\b\D{0,24}?(\d{1,6})\s*%?", re.I)
#: A type cell that says the row is computed, not asked.
_DERIVED_TYPE = re.compile(r"\b(?:derived|computed|calculated|hidden)\b", re.I)

_TYPE_WORDS = [
    ("matrix", QuestionType.GRID_SINGLE),
    ("grid", QuestionType.GRID_SINGLE),
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
    ("rank", QuestionType.RANK),
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
    #: The base as a condition, when it states one coded condition.
    show_if: Expression | None = None
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
    raw = match.group(2) or ""
    known = {o.code for o in options}

    # A screen-out can sit on another question's answer: "TERMINATE if 13-99
    # selected at SS_REVENUE", written in the notes of the question that comes
    # after it. Resolve the target FIRST, because it decides whose punch codes a
    # range should be read against.
    named = _TERMINATE_AT_RE.search(notes or "")
    other_question = bool(named and named.group(1).upper() != qid.upper())
    on = match.group("on")
    if on and on.upper() != qid.upper() and not other_question:
        # "TERMINATE if SS_REVENUE = 3": written on another question's answer.
        other_question, named = True, re.match(r"(.+)", on)

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
    negated = "not" in (match.group(3) or "").lower()

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
    low = _LAYOUT_QUALIFIER.sub(" ", (stated or "").strip().lower())
    if _DISPLAY_ONLY.search(low):
        return QuestionType.INFO, f"Q type: {stated.strip()}"
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
        low, high = (int(g.replace(",", "")) for g in allowed.groups())
        if low <= high:
            return {"min": low, "max": high}
    digits = _DIGIT_MAX_RE.search(text)
    if digits:
        return {"min": 0, "max": 10 ** int(digits.group(1)) - 1}
    return {}


_RULE_SPLIT = re.compile(r"\b(?:terminate|term|screen\s*-?\s*out|flag)\b", re.I)
# "less than $500", "under 18", "more than 1,000"
_WORD_BOUND_RE = re.compile(
    r"\b(less\s+than|fewer\s+than|under|below|more\s+than|greater\s+than|over|above)\s+"
    r"\$?\s*(\d[\d,]*)",
    re.I,
)


def _parse_numeric_terminate(
    notes: str, qid: str, opt_outs: list[Option]
) -> tuple[Expression | None, bool]:
    """ "TERMINATE if age<25 or age>?? OR if 99 selected" on a typed number.

    Returns the screen-out it can state exactly, and whether part of it could
    NOT be read. Each TERMINATE sentence is read on its own: "TERMINATE if less
    than $500 / Flag if greater than $1,000,000 / TERMINATE if 13-99 selected at
    SS_REVENUE" is two screen-outs and a flag, and reading them as one applied
    the revenue codes to this question. A bound written "??" is a number the
    scripter never filled in: the rest still holds, and the gap goes to a person.
    """
    pieces = _RULE_SPLIT.split(notes or "")
    keywords = _RULE_SPLIT.findall(notes or "")
    rules = [text for word, text in zip(keywords, pieces[1:], strict=True)
             if word.lower() != "flag" and re.match(r"\s*(?:if|when)\b", text, re.I)]
    if not rules:
        return None, False
    parts: list[Expression] = []
    unread = False
    known = {o.code for o in opt_outs}
    for rule in rules:
        before = len(parts)
        named = _TERMINATE_AT_RE.search(rule)
        if named and named.group(1).upper() != qid.upper():
            # On another question's answer: kept in words for a person.
            unread = True
            continue
        bounds = [(op, b) for op, b in _NUMERIC_BOUND_RE.findall(rule)]
        for word, number in _WORD_BOUND_RE.findall(rule):
            low = word.lower()
            op = "<" if low.startswith(("less", "fewer", "under", "below")) else ">"
            bounds.append((op, number.replace(",", "")))
        for op, bound in bounds:
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
        codes = [int(c) for c in _CODE_SELECTED_RE.findall(rule) if int(c) in known]
        lowered = rule.lower()
        codes += [o.code for o in opt_outs
                  if o.label and o.label.lower() in lowered and o.code not in codes]
        if codes:
            parts.append(Expression(op="in", qid=qid, values=codes))
        if len(parts) == before and not any(b.startswith("?") for _, b in bounds):
            unread = True
    if not parts:
        return None, True
    expression = parts[0] if len(parts) == 1 else Expression(op="or", children=parts)
    expression.verbatim_text = notes.strip()
    return expression, unread


def _read_directive(line, option, option_bases, list_sources) -> bool:
    """Act on one bracketed directive. True when it was one."""
    directive = option_directives.read(line)
    if directive is None:
        return False
    if directive["kind"] == "base" and option is not None:
        option_directives.attach(option, line)
        option_bases.append({"code": option.code, "verbatim": directive["verbatim"],
                             "resolved": option.show_if is not None})
        return True
    if directive["kind"] == "list_from":
        list_sources.append(directive)
        return True
    return False


def _split_trailing_directive(value: str, row: list[str]) -> tuple[str, str]:
    """(the answer's label, the directive written under it in the same cell).

    Word keeps both lines in one cell, and `_cells` joins them with a space to
    make the row readable — which is right for every other row and wrong here,
    because the second line is a rule about the answer rather than part of it.
    """
    for raw in row:
        lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
        if len(lines) == 2 and lines[1].startswith("[") and value.startswith(lines[0]):
            return lines[0], lines[1]
    return value, ""


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
    base_clash: list[str] = []
    list_sources_found: list[dict] = []
    seen_qid_row = False
    in_row_list = False
    in_list = False

    table_text = "\n".join(" ".join(_cells(row)) for row in rows)
    definition_col = _definition_column(rows)
    tag_terminate: list[int] = []
    for row in rows:
        cells = _cells(row)

        # A whole answer list in one cell ("1: Yes   2: No", "1 = Yes // 2 = No").
        # Whatever the row's label is, a cell that is nothing BUT coded answers is
        # a list; a labelled list marker is enough on its own with a single answer.
        if len(cells) > 1 and cells[0] and not _CODE_RE.match(cells[0]) and (
            _field_for(cells[0], patterns) is None
        ):
            low_label = cells[0].strip().lower()
            is_marker = any(low_label.startswith(m) for m in _LIST_MARKERS)
            inline = _inline_items(row, 1 if is_marker else 2)
            if inline:
                in_list = True
                in_row_list = any(low_label.startswith(m) for m in _ROW_LIST_MARKERS)
                for code, label, tags, derivation in inline:
                    option = Option(
                        code=code, label=label, definition=derivation,
                        exclusive=tags.get("exclusive", False),
                        anchored=tags.get("anchored", False),
                    )
                    (row_items if in_row_list else options).append(option)
                    last_option = option
                    if tags.get("terminate") and not in_row_list:
                        tag_terminate.append(code)
                continue

        # A marker line switches which list the codes below it belong to, and it
        # is written with an EMPTY first cell — the label sits in the merged
        # columns beside it — so it has to be read BEFORE the blank-code skip
        # below, or the switch never happens and both lists pour into one.
        marker = next((c.strip().lower() for c in cells if c.strip()), "")
        if any(marker.startswith(m) for m in _LIST_MARKERS):
            in_row_list = any(marker.startswith(m) for m in _ROW_LIST_MARKERS)
            in_list = True

        if not cells or not cells[0]:
            # A directive can sit in the value column with NO code beside it:
            #     |    | [INSERT CURRENTLY USED BRANDS FROM DA_PLATFORM_USAGE 9-15] |
            # Skipping every blank-code row dropped it, so a list that says
            # where the rest of its items come from said nothing at all.
            bracketed = next((c for c in cells[1:] if c.strip().startswith("[")), "")
            if bracketed:
                _read_directive(
                    bracketed, last_option, option_bases, list_sources_found
                )
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
                # The directive may share the answer's own cell, on the line
                # below it: "Google Ads⏎[IF GOOGLE ADS CURRENT USER]". Joined
                # into the label it became part of what the respondent reads,
                # and the base it states was never applied.
                value, inline = _split_trailing_directive(value, row)
                target = row_items if in_row_list else options
                target.append(
                    Option(
                        code=int(cells[0]),
                        label=value,
                        definition=_definition_in(row, definition_col),
                    )
                )
                last_option = target[-1]
                if inline:
                    _read_directive(
                        inline, last_option, option_bases, list_sources_found
                    )
            continue

        name = _field_for(cells[0], patterns)
        if name:
            value = _value_of(row)
            if name == "base" and _ALL_RESPONDENTS.match(value):
                # "All respondents | If not All, specify: HID_GOOGLE_USER = 2 or 3"
                # — the scripter filled in a base and forgot to switch the
                # dropdown. The written base is the intent; say it was a clash.
                written = (_PROMPT_PREFIX.sub("", c).strip() for c in cells[2:]
                           if _PROMPT_PREFIX.match(c))
                stated = next((w for w in written if w), "")
                if stated:
                    base_clash.append(f"marked '{value}' but specifies: {stated}")
                    value = stated
            found.setdefault(name, value)
            if name in ("show", "notes") and _SCRIPTER_DIRECTIVE.search(value):
                # "Logic: Randomize response list // Max 3 selections": the cell
                # holds programming instructions, not (only) a routing rule.
                # Keep them where the randomisation, limit and range readers look.
                if name == "show":
                    found["notes"] = " // ".join(filter(None, [found.get("notes"), value]))
                if _RANDOMIZE_WORD.search(value):
                    found.setdefault("randomize", value)
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
    if notes and not found.get("randomize") and _RANDOMIZE_WORD.search(notes):
        # "Scripter instructions: Rotate answers" — randomisation said in the notes.
        found["randomize"] = notes

    # Two lists means a grid: the variable list is rated ON the response list.
    # One list is an ordinary answer list, exactly as before.
    #
    # The third case is what DA_GOAL_PLATFORM taught: a variable list whose
    # RESPONSE list is a directive rather than codes —
    #
    #     Variable list:  1 Google Ads / 2 Meta Ads / 99 None of these
    #     Response list:  [INSERT GOALS SELECTED AT DA_GOALS]
    #
    # "row_items and options" was false, so the assembly fell to ([], []) and
    # three real rows were DISCARDED — and because nothing was recorded as
    # missing, the question reported itself complete. A grid with no rows and
    # no columns reached the reviewer looking like a question the parser had
    # failed on, when in fact the document stated its rows plainly.
    stated_shape = (found.get("type") or "").lower()
    if row_items and options:
        grid_rows, grid_cols = row_items, options
    elif row_items and any(w in stated_shape for w in ("grid", "matrix", "carousel")):
        # The rows are real; the columns come from the directive, which is
        # recorded in `list_from` for the pass that fills lists.
        grid_rows, grid_cols = row_items, []
    elif row_items and not options and _SUM_BOXES.search(stated_shape):
        # "Number entry per statement (sum)": one box per item, not a choice.
        grid_rows, grid_cols = [], []
    elif row_items:
        # Labelled "variable list" but nothing rates it and the type does not
        # say grid: then that list IS the answer list.
        options = options or row_items
        grid_rows, grid_cols = [], []
    else:
        grid_rows, grid_cols = [], []

    if _DERIVED_TYPE.search(found.get("type", "")) and not found.get("show"):
        found["show"] = found["type"]  # "Derived variable (not shown)" says it outright

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
            if grid_cols
            else f"a variable list of {len(grid_rows)}, stated as a grid"
        )
        if not grid_cols and "cols" not in fields.missing:
            # Say so. A grid with rows and no columns is not complete, and
            # reporting it as complete is how it reached a reviewer silently.
            fields.missing.append("cols")
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
                "part of this screen-out could not be applied (a limit written '??', "
                "or a rule on another question's answer) — the rest is applied; "
                "set the missing part by hand"
            )
    else:
        fields.terminate_if = _parse_terminate(notes, options, qid)
        if fields.terminate_if is None and tag_terminate:
            # "5: Student / not employed [SCREEN OUT]" on the answers themselves.
            fields.terminate_if = Expression(
                op="in", qid=qid, values=sorted(set(tag_terminate)),
                verbatim_text="screen-out tags on answers " + ", ".join(map(str, tag_terminate)),
            )
            notes = notes or fields.terminate_if.verbatim_text or ""
    if row_items and not grid_rows and fields.qtype is QuestionType.NUMERIC:
        # One box per item: the items are ROWS of a number question.
        fields.rows, fields.options = list(row_items), []
        total = _STATED_SUM.search(table_text) if _SUM_BOXES.search(stated_shape) else None
        fields.numeric_range = _numeric_range(table_text)
        if total:
            n = int(total.group(1))
            fields.numeric_range = {"min": 0, "max": n, **fields.numeric_range, "sum": n}
            fields.evidence["sum"] = total.group(0).strip()
    if fields.qtype is QuestionType.NUMERIC and not fields.numeric_range:
        # "Numeric OE" with "Allowable range 0-30" in the logic cell.
        fields.numeric_range = _numeric_range(table_text)
        if fields.numeric_range:
            fields.evidence["range"] = (
                f"{fields.numeric_range['min']}-{fields.numeric_range['max']}"
            )
    if fields.terminate_if is not None:
        fields.terminate_verbatim = notes.strip()
        fields.evidence["terminate"] = notes.strip()
    # "Among who: Other >>>" pointing at "auto=punch those who select only one at
    # DA_GOALS": the cell is the rule for an AUTOMATIC ANSWER, not a base the
    # reader failed to understand. Sending "Other >>>" to a model as the base
    # is how it came back as an unclear one.
    auto = auto_answer_lines(table_text)
    # With ">>>" read, the base cell holds the rule itself; either way it is
    # not a base, and must not be sent to a model as one.
    if auto and fields.base and (
        re.fullmatch(r"other\W*", fields.base, re.IGNORECASE) or auto_answer_lines(fields.base)
    ):
        fields.base = None
        fields.evidence["auto_answer"] = auto[0]
    if fields.base:
        fields.evidence["base"] = f"Among who: {fields.base}"
    if base_clash:
        fields.evidence["base_clash"] = base_clash[0]

    if not fields.text:
        fields.missing.append("text")
    if fields.qtype is None:
        fields.missing.append("type")
    # A base in prose ("those aware of the brand") needs judgment; a blank base
    # means everyone and needs none; a coded one ("S1=1-3") is already usable.
    if fields.base and not _ALL_RESPONDENTS.match(fields.base):
        fields.show_if = base_condition(fields.base) or _routing_condition(fields.base)
        if fields.show_if is None:
            fields.missing.append("base")
    if notes and re.search(r"terminate|term\b|screen\s*-?\s*out", notes, re.IGNORECASE):
        if fields.terminate_if is None and "terminate_gap" not in fields.evidence:
            # It states a screen-out we could not parse — never silently drop it.
            fields.missing.append("terminate")

    return fields
