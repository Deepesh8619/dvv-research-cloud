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
                 "code frame", "codes", "scale")


def _norm_label(value: str) -> str:
    """Comparable label: lowercase, no punctuation, single-spaced."""
    low = (value or "").strip().lower().rstrip(":")
    low = re.sub(r"[^a-z0-9\s]", " ", low)
    return " ".join(low.split())


def _field_for(label: str) -> str | None:
    """Which field a row label populates, or None if we do not know it."""
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
    r"(?:if|when)?\s*(?:code[s]?\s*)?([\d,\s&and]+?)\s*"
    r"(not\s+selected|is\s+not|selected|chosen|$)",
    re.IGNORECASE,
)

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
    terminate_if: Expression | None = None
    terminate_verbatim: str | None = None
    base: str | None = None
    show: str | None = None
    randomize: str | None = None
    notes: str | None = None
    # Labels we did not recognise, kept so an unknown client convention is
    # visible (and teachable via the correction loop) rather than silently lost.
    extras: dict[str, str] = field(default_factory=dict)
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
    codes = [int(c) for c in re.findall(r"\d+", match.group(1) or "")]
    if not codes:
        return None
    negated = "not" in (match.group(2) or "").lower()

    known = {o.code for o in options}
    if known and not set(codes).issubset(known):
        return None  # refers to codes this question does not have — do not guess

    # The condition is on THIS question's own answer ("TERMINATE if 5,6
    # selected" means codes of this question), so it must name this qid. A
    # placeholder like "__self__" is not a real qid, and the cross-chunk
    # resolver in extract_survey_definition nulls any expression referencing a
    # qid that exists nowhere — which silently discarded every terminate this
    # parser extracted. Caught only by running a full extraction end to end.
    return Expression.from_dict(
        {
            "op": "not_in" if negated else "in",
            "qid": qid,
            "value": codes,
            "verbatim_text": (notes or "").strip(),
        }
    )


# An explicit request for typed input, used to tell an open end apart from a
# display screen when neither has a response list.
_OPEN_ENTRY_RE = re.compile(
    r"\bopen[- ]?end|\btextbox\b|\bOE\b|specify|please describe|"
    r"in your own words|enter|type|_\s*_\s*_",
    re.I,
)


def _infer_type(
    stated: str, options: list[Option], text: str | None
) -> tuple[QuestionType | None, str]:
    """Resolve the question type and the fragment that decided it.

    Scripters frequently leave the cell blank, so the answer often comes from
    the wording instead — the reviewer needs to be told which.
    """
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


def parse_spec_table(block: DocumentBlock) -> SpecTableFields | None:
    """Extract everything a labelled spec table states. None if it is not one."""
    rows = block.rows or []
    if not rows:
        return None

    found: dict[str, str] = {}
    extras: dict[str, str] = {}
    options: list[Option] = []
    unlabelled: list[str] = []
    seen_qid_row = False

    for row in rows:
        cells = _cells(row)
        if not cells or not cells[0]:
            continue

        if _CODE_RE.match(cells[0]):
            value = _value_of(row)
            if value:
                options.append(Option(code=int(cells[0]), label=value))
            continue

        name = _field_for(cells[0])
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

    fields = SpecTableFields(
        qid=qid,
        text=text or None,
        options=options,
        base=found.get("base") or None,
        show=found.get("show") or None,
        randomize=found.get("randomize") or None,
        notes=notes or None,
        extras=extras,
    )
    fields.qtype, type_evidence = _infer_type(found.get("type", ""), options, text)
    if type_evidence:
        fields.evidence["type"] = type_evidence
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
        if fields.terminate_if is None:
            # It states a screen-out we could not parse — never silently drop it.
            fields.missing.append("terminate")

    return fields
