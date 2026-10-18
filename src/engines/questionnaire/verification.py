"""Check an extracted question against the document it came from.

The existing verification pass asks a model "is this faithful?". That is a
generalist judging work with no stated standard, and it answers confidently
either way — the same document verified three times gave three different
question counts before today.

A scripter checking a colleague's work does not do that. They read the source
line by line and ask specific questions with yes-or-no answers:

    every option in this definition — is it written in the document?
    every option line in the document — did it survive into the definition?
    do the codes match the ones the document wrote?
    does the type match what the wording asks for?
    does the terminate name a code that exists?
    is there a scripter's bracket left in what the respondent will read?

Each of those is decidable from the chunk, with the offending line as evidence.
So they are checks, not prompts. What a model cannot settle either — whether
wording was subtly reworded, whether an implied option was invented — is left
to the model pass, which now runs only when this checklist cannot cover the
question.

Every check names the evidence that failed it. A finding with no line to point
at is an opinion, and an opinion is what we are replacing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.survey_definition import GRID_TYPES, Question, QuestionType

# A line that offers an answer: "1. Yes", "98: Other (specify)", "- Retailer A".
_OPTION_LINE = re.compile(r"^\s*(?:(?P<code>\d{1,3})\s*[.):]|[-•*])\s+(?P<label>\S.*)$")
_TAG = re.compile(r"\[[^\]]*\]")
# Two or more cells: what the loader writes when it serializes a table.
_TABLE_ROW = re.compile(r"\S\s*\|\s*\S.*\|")
_MULTI_WORDING = re.compile(r"select\s+all\s+that\s+apply|choose\s+all|multiple\s+answers", re.I)
_SINGLE_WORDING = re.compile(r"select\s+one|choose\s+one|select\s+a\s+single", re.I)
_SCRIPTER_TAG = re.compile(r"\[\s*(?:PN|ANCHOR|EXCLUSIVE|TERMINATE|SHOW\s+IF|ASK\s+IF)", re.I)


@dataclass
class Check:
    """One decidable question about the extraction, with its evidence."""

    name: str
    passed: bool
    detail: str = ""
    evidence: str = ""


def _normalise(text: str) -> str:
    """Compare on what a person would call the same wording."""
    without_tags = _TAG.sub(" ", text or "")
    return " ".join(re.sub(r"[^\w\s]", " ", without_tags).lower().split())


def _source_option_lines(chunk_text: str) -> list[tuple[int | None, str]]:
    # A chunk holding a serialized table cannot be read line by line for
    # answers: "1. Pacific (WA, OR, CA, AK, HI)" is a row of a region reference
    # table, and its leading number is a row index, not a punch code. Read as
    # answers, that table reported a code mismatch on every row — the loudest
    # possible finding about a question nobody is asked. Judging the rows of a
    # table is the grid reader's job, and it has the real cells to do it with;
    # this checklist has a flattened string and should not guess from it.
    if any(_TABLE_ROW.search(line) for line in (chunk_text or "").splitlines()):
        return []

    out: list[tuple[int | None, str]] = []
    for line in (chunk_text or "").splitlines():
        match = _OPTION_LINE.match(line)
        if not match:
            continue
        label = _normalise(match.group("label"))
        if not label:
            continue
        code = match.group("code")
        out.append((int(code) if code else None, label))
    return out


def verify_against_source(question: Question, chunk_text: str) -> list[Check]:
    """Every check that is decidable from this question's own source text."""
    checks: list[Check] = []
    haystack = _normalise(chunk_text)
    source_options = _source_option_lines(chunk_text)

    # Checks 1 and 2 compare against the answer lines the document writes. When
    # it writes none — an implied Yes/No a scripter never spelled out, or a
    # question rebuilt from the data map — there is nothing to compare against,
    # and a check with no evidence available must not produce a finding. It goes
    # to the model instead, which is what the model is for.
    #
    # A hidden/derived variable has no respondent answers to capture: the lines
    # under it are its DERIVATION ("Google Ads current user: DA_USAGE = 1,2,3,4"),
    # which the reader files as definitions on the values. Counting them as
    # answers the definition "dropped" flagged eight HID_ variables of one
    # real document as unfaithful when nothing had been dropped.
    derived = "DERIVED_VARIABLE" in (question.flags or ()) or (
        question.qid.upper().startswith("HID_")
    )
    if source_options and not derived:
        checks.extend(_answer_list_checks(question, haystack, source_options))
    return checks + _always_decidable(question, chunk_text)


def _answer_list_checks(
    question: Question, haystack: str, source_options: list[tuple[int | None, str]]
) -> list[Check]:
    """The checks that need the document to have written its answers down.

    When it writes none — an implied Yes/No a scripter never spelled out, or a
    question rebuilt from the data map — there is nothing to compare against,
    and a check with no evidence available must not produce a finding.
    """
    checks: list[Check] = []

    # 1. Nothing in the definition that the document does not say.
    invented = [
        o.label
        for o in question.options or []
        if _normalise(o.label) and _normalise(o.label) not in haystack
    ]
    checks.append(
        Check(
            "options_are_in_the_source",
            not invented,
            f"{len(invented)} option(s) appear in the definition but not in the document",
            "; ".join(invented[:3]),
        )
    )

    # 2. Nothing the document offers that the definition dropped. This is the
    #    check that would have caught an answer silently missing from a live
    #    instrument: five options written, four extracted, and a question that
    #    looked complete.
    captured = {_normalise(o.label) for o in question.options or []}
    captured |= {_normalise(o.label) for o in (question.rows or [])}
    dropped = [label for _, label in source_options if label not in captured]
    if question.type not in GRID_TYPES:
        checks.append(
            Check(
                "source_options_all_captured",
                not dropped,
                f"{len(dropped)} answer line(s) in the document are not in the definition",
                "; ".join(dropped[:3]),
            )
        )

    # 3. Codes the document states are the codes the data will carry.
    written = {label: code for code, label in source_options if code is not None}
    mismatched = [
        f"{o.label}: document says {written[_normalise(o.label)]}, definition says {o.code}"
        for o in question.options or []
        if _normalise(o.label) in written and written[_normalise(o.label)] != o.code
    ]
    if written:
        checks.append(
            Check(
                "codes_match_the_document",
                not mismatched,
                "a punch code differs from the one the document wrote",
                "; ".join(mismatched[:3]),
            )
        )

    return checks


def _always_decidable(question: Question, chunk_text: str) -> list[Check]:
    """Checks that hold whether or not the document lists its answers."""
    checks: list[Check] = []

    # 4. The type the wording asks for.
    wording = f"{question.text or ''} {chunk_text or ''}"
    if _MULTI_WORDING.search(wording) and question.type not in (
        QuestionType.MULTI,
        QuestionType.GRID_SINGLE,
        QuestionType.GRID_MULTI,
    ):
        checks.append(
            Check("type_matches_the_wording", False,
                  f'the document says "select all that apply"; type is {question.type.value}',
                  _MULTI_WORDING.search(wording).group(0))
        )
    elif _SINGLE_WORDING.search(wording) and question.type is QuestionType.MULTI:
        checks.append(
            Check("type_matches_the_wording", False,
                  'the document says "select one"; type is multi',
                  _SINGLE_WORDING.search(wording).group(0))
        )
    else:
        checks.append(Check("type_matches_the_wording", True))

    # 5. A grid answers per row, with the columns as its scale.
    if question.type in GRID_TYPES:
        checks.append(
            Check(
                "grid_has_rows_and_columns",
                bool(question.rows and question.cols),
                "a grid with no rows emits no data columns at all",
                f"rows={len(question.rows or [])} cols={len(question.cols or [])}",
            )
        )

    # 6. A screen-out that names a code nobody can pick never fires.
    # On a typed number, "under 500" is a value, not answer code 500.
    codes = {o.code for o in question.options or []}
    if question.terminate_if is not None and codes and question.type is not QuestionType.NUMERIC:
        named = _codes_named(question.terminate_if, question.qid)
        unknown = sorted(named - codes)
        checks.append(
            Check(
                "terminate_names_a_real_code",
                not unknown,
                "the screen-out names a code this question does not offer",
                f"{unknown}" if unknown else "",
            )
        )

    # 7. Nothing addressed to the scripter left in what a respondent reads.
    leftover = [
        text
        for text in [question.text or ""] + [o.label for o in question.options or []]
        if _SCRIPTER_TAG.search(text)
    ]
    checks.append(
        Check(
            "no_scripter_tag_in_respondent_text",
            not leftover,
            "a scripter directive is still inside text the respondent will read",
            "; ".join(leftover[:2]),
        )
    )
    return checks


def _codes_named(expression, qid: str) -> set[int]:
    """Codes a condition tests against THIS question."""
    found: set[int] = set()
    if getattr(expression, "qid", None) == qid:
        if isinstance(getattr(expression, "value", None), int):
            found.add(expression.value)
        for value in expression.values or []:
            if isinstance(value, int):
                found.add(value)
    for child in getattr(expression, "children", None) or []:
        found |= _codes_named(child, qid)
    return found


def failed(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.passed]


def covers_the_question(question: Question, checks: list[Check]) -> bool:
    """True when the checklist has actually examined this extraction.

    A question with no options and no conditions gives the checklist almost
    nothing to test, and "nothing failed" would be a hollow pass. Those still go
    to the model, which is the point: the checklist takes the work it can settle
    and leaves the rest, rather than claiming everything.
    """
    if question.type in GRID_TYPES:
        return bool(question.rows and question.cols)
    if question.type in (QuestionType.OPEN_TEXT, QuestionType.NUMERIC, QuestionType.INFO):
        return bool(question.text)
    return bool(question.options) and len(checks) >= 4
