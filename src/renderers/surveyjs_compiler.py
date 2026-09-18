"""SurveyJS compiler.

Compiles a `SurveyDefinition` (our authored instrument model) into a SurveyJS
survey JSON model — the schema consumed by the SurveyJS runtime/creator
(https://surveyjs.io). Blocks become pages, questions become typed elements,
and routing `Expression`s become SurveyJS `visibleIf` condition strings.

This is a pure transformation: no I/O, no side effects, deterministic dict out.
Termination logic (`terminate_if`) and quotas are intentionally *not* emitted:
SurveyJS has no native mid-survey terminate, and terminates/quotas are meant to
be server-authoritative (a future respondent-runtime task enforces them) since
quota cells cost money if gamed client-side. `terminate_if` is therefore
dropped here by design, not by omission.
"""

from __future__ import annotations

import random
import re
from typing import Any

from src.core.survey_definition import (
    Block,
    Expression,
    Question,
    QuestionType,
    SurveyDefinition,
)
from src.engines.questionnaire.grid_subquestions import without_question_label

# Square brackets are a SCRIPTING convention, not respondent language. Across
# every instrument we have — client documents and public federal ones alike —
# wording aimed at the respondent uses parentheses ("Other (please specify)")
# and wording aimed at the scripter uses brackets ("[SP]", "[IF S2 = 2]",
# "[Num box 1900-2014]", "[SHOW ONLY ITEMS SELECTED AT Q1]"). So nothing
# bracketed is ever shown to a person: the compiler removes it on the way out,
# while the stored question keeps its original wording for review.
_BRACKETED = re.compile(r"\[[^\]]*\]")
_WHITESPACE = re.compile(r"\s+")
# " ?" and " ," left behind once a directive is lifted out of a sentence.
_STRANDED_PUNCTUATION = re.compile(r"\s+([?.,;:!])")

# Question types whose answer is a LIST, so a mask tests membership rather than
# equality.
_MULTI_VALUED = frozenset({QuestionType.MULTI, QuestionType.RANK})


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "")).strip().casefold()


def _resolve_pipes(text: str, pipes: list[dict], by_qid: dict[str, Question]) -> str:
    """Turn "[INSERT RESPONSE FROM Q2]" into the marker SurveyJS interpolates.

    The compiler used to assume piping needed no work because SurveyJS already
    substitutes `{qid}` markers "present in question text". They are never
    present: a questionnaire writes the instruction in prose, and what reached
    the respondent was the instruction itself.
    """
    for pipe in pipes:
        source = pipe.get("source_qid")
        if not source or source not in by_qid:
            continue  # unresolvable — the strip below removes it
        wanted = _normalize(pipe.get("verbatim") or "")
        for group in _BRACKETED.findall(text):
            body = _normalize(group[1:-1])
            if body == wanted or (wanted and wanted in body) or source.casefold() in body:
                text = text.replace(group, "{" + source + "}", 1)
                break
    return text


def _for_respondent(text: str, pipes: list[dict], by_qid: dict[str, Question]) -> str:
    """`text` as a person should read it: pipes resolved, directives removed."""
    piped = _resolve_pipes(text or "", pipes, by_qid)
    stripped = _BRACKETED.sub(" ", piped)
    return _STRANDED_PUNCTUATION.sub(r"\1", _WHITESPACE.sub(" ", stripped)).strip()


def _choices_visible_if(mask: dict, by_qid: dict[str, Question]) -> str | None:
    """A per-choice condition that narrows a masked list to what was answered.

    SurveyJS evaluates `choicesVisibleIf` once per option with `{item}` bound to
    that option's value, which is exactly the shape a mask needs. Returns None
    when the mask cannot be expressed — a full list has nothing to narrow, and a
    source we cannot see is not something to guess at.
    """
    mode = mask.get("mode")
    if mode == "full_list":
        return None
    against = mask.get("exclude_qid") if mode == "list_except" else mask.get("source_qid")
    source = by_qid.get(against or "")
    if source is None:
        return None
    keeps_what_was_chosen = mode == "selected"
    if source.type in _MULTI_VALUED:
        operator = "contains" if keeps_what_was_chosen else "notcontains"
        return f"{{{against}}} {operator} {{item}}"
    operator = "=" if keeps_what_was_chosen else "<>"
    return f"{{{against}}} {operator} {{item}}"


def _techniques(question: Question) -> dict:
    return (question.validation or {}).get("techniques") or {}


# SurveyJS operator strings keyed by our leaf comparison ops.
_LEAF_OPERATORS = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "lt": "<",
}


def _format_value(value: Any) -> str:
    """Render a scalar into SurveyJS condition-string syntax.

    Numbers are bare; everything else is single-quoted (SurveyJS accepts single
    quotes for string literals in its expression grammar).
    """
    if isinstance(value, bool):
        # bool is an int subclass — guard before the numeric branch.
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'{}'".format(str(value).replace("'", "\\'"))


def _expression_to_visibleif_string(expr: Expression) -> str:
    """Translate a routing `Expression` into a SurveyJS `visibleIf` string.

    Uses SurveyJS's condition grammar: `{qid}` variable references, `= <> > <`
    comparisons, `contains` for membership, `and`/`or`/`not`, `anyof` for
    multi-value `in`. Logical sub-expressions are parenthesised so precedence is
    unambiguous.
    """
    op = expr.op

    if op == "and":
        parts = [_expression_to_visibleif_string(c) for c in expr.children or []]
        return " and ".join(f"({p})" for p in parts)
    if op == "or":
        parts = [_expression_to_visibleif_string(c) for c in expr.children or []]
        return " or ".join(f"({p})" for p in parts)
    if op == "not":
        parts = [_expression_to_visibleif_string(c) for c in expr.children or []]
        inner = " and ".join(f"({p})" for p in parts)
        return f"not ({inner})"

    # A condition on ONE row of a grid names that row: SurveyJS addresses a
    # matrix cell as {question.row}, and without it the test runs against the
    # whole {row: column} answer and matches nobody.
    ref = f"{{{expr.qid}.{expr.row}}}" if expr.row is not None else f"{{{expr.qid}}}"

    if op == "selected":
        # "was option X chosen" — works for both single (=) and multi (contains);
        # `contains` in SurveyJS is true when the answer array holds the value.
        return f"{ref} contains {_format_value(expr.value)}"
    if op == "in":
        values = expr.values or []
        formatted = ", ".join(_format_value(v) for v in values)
        # `anyof` tests intersection of the answer with a value set.
        return f"{ref} anyof [{formatted}]"

    operator = _LEAF_OPERATORS.get(op)
    if operator is None:
        raise ValueError(f"cannot translate expression op to visibleIf: {op!r}")
    return f"{ref} {operator} {_format_value(expr.value)}"


def _choices(options: list, by_qid: dict[str, Question]) -> list[dict[str, Any]]:
    # An option's label carries scripter tags too — "Other (Please specify):
    # [ TXT ] ____" — so it goes through the same public-facing filter.
    compiled: list[dict[str, Any]] = []
    for o in options:
        item: dict[str, Any] = {
            "value": o.code,
            "text": _for_respondent(o.label, [], by_qid),
        }
        if o.show_if is not None:
            # Per-item visibility: SurveyJS honours `visibleIf` on a choice and
            # on a matrix row, so a condition written against one answer is
            # enforced where the respondent actually is, not left as a note.
            item["visibleIf"] = _expression_to_visibleif_string(o.show_if)
        compiled.append(item)
    return compiled


def _numeric_validators(validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Build SurveyJS `numericvalidator`(s) from a numeric question's validation."""
    if not validation:
        return []
    minimum = validation.get("min")
    maximum = validation.get("max")
    if minimum is None and maximum is None:
        return []
    validator: dict[str, Any] = {"type": "numeric"}
    if minimum is not None:
        validator["minValue"] = minimum
    if maximum is not None:
        validator["maxValue"] = maximum
    return [validator]


def _compile_question(question: Question, by_qid: dict[str, Question]) -> dict[str, Any]:
    """Compile one `Question` into its SurveyJS element dict."""
    qtype = question.type
    techniques = _techniques(question)
    title = _for_respondent(question.text, techniques.get("pipes") or [], by_qid)

    # Info content carries no answer — no `name`, no `isRequired`.
    if qtype == QuestionType.INFO:
        return {"type": "html", "html": title}

    element: dict[str, Any] = {"name": question.qid, "title": title}

    if qtype == QuestionType.SINGLE:
        element["type"] = "radiogroup"
        element["choices"] = _choices(question.options, by_qid)
    elif qtype == QuestionType.MULTI:
        element["type"] = "checkbox"
        element["choices"] = _choices(question.options, by_qid)
    elif qtype == QuestionType.DROPDOWN:
        element["type"] = "dropdown"
        element["choices"] = _choices(question.options, by_qid)
    elif qtype == QuestionType.RANK:
        element["type"] = "ranking"
        element["choices"] = _choices(question.options, by_qid)
    elif qtype == QuestionType.GRID_SINGLE:
        element["type"] = "matrix"
        element["columns"] = _choices(question.cols or [], by_qid)
        # A definition stored before grids like Q2a/Q2b were split still heads
        # its columns "Q2a. ever heard of?" — a label the respondent cannot read.
        for column in element["columns"]:
            column["text"] = without_question_label(column["text"])
        element["rows"] = _choices(question.rows or [], by_qid)
    elif qtype == QuestionType.NUMERIC:
        element["type"] = "text"
        element["inputType"] = "number"
        validators = _numeric_validators(question.validation)
        if validators:
            element["validators"] = validators
    elif qtype == QuestionType.OPEN_TEXT:
        # A max-length constraint reads as a short single-line answer; otherwise
        # default to a multi-line comment box.
        max_len = (question.validation or {}).get("max_length")
        if max_len is not None:
            element["type"] = "text"
            element["maxLength"] = max_len
        else:
            element["type"] = "comment"
    else:  # pragma: no cover - defensive; enum is exhaustive above
        raise ValueError(f"unsupported question type: {qtype!r}")

    element["isRequired"] = question.required

    if question.randomize_options and "choices" in element:
        # Ordered HERE, once per respondent (the survey is compiled per entry),
        # rather than with SurveyJS's choicesOrder: an anchored option must keep
        # its place. "None of the above [ANCHOR]" shuffled into the middle of
        # the list is a scripting error a client would reject the data for, and
        # the library has no way to pin one choice.
        element["choices"] = _shuffled_choices(question, element["choices"])

    if question.show_if is not None:
        element["visibleIf"] = _expression_to_visibleif_string(question.show_if)

    # A mask narrows the option list to what this respondent said earlier. It
    # was being detected, stored, and then ignored — so everyone was offered
    # every option, and picked ones they had never named. The first condition
    # we can express wins; a question carrying two contradictory masks is a
    # scripting error for a human, not something to silently AND together.
    if "choices" in element:
        for mask in techniques.get("masks") or []:
            condition = _choices_visible_if(mask, by_qid)
            if condition:
                element["choicesVisibleIf"] = condition
                break

    return element


# Content that exists for the scripter, not the respondent. A derived variable
# is computed from other answers and its "wording" is an instruction — showing
# "CODE AGE - BASED ON RESPONSES AT S1" to a member of the public is a defect,
# not display text. `info` alone is not enough to exclude: a genuine intro or
# transition screen is also `info` and SHOULD be shown.
_SCRIPTER_ONLY_FLAGS = frozenset({"DERIVED_VARIABLE"})
# The flag is set at extraction, so a definition stored BEFORE that rule existed
# carries none — and a respondent was shown a page reading "CODE AGE". The
# wording is the same signal the extractor uses, and checking it here means an
# old definition stops leaking without being re-extracted first.
_SCRIPTER_ONLY_WORDING = re.compile(r"^[^a-z]*\bCODE\b[^a-z]*$")


def _is_respondent_facing(question: Question) -> bool:
    if _SCRIPTER_ONLY_FLAGS & set(question.flags or ()):
        return False
    if question.type is QuestionType.INFO and _SCRIPTER_ONLY_WORDING.match(
        (question.text or "").strip()
    ):
        return False
    return True


def _compile_block(block: Block, survey: SurveyDefinition) -> dict[str, Any]:
    """Compile one `Block` into a SurveyJS page dict."""
    elements: list[dict[str, Any]] = []
    by_qid = {q.qid: q for q in survey.questions}
    for qid in block.question_ids:
        if qid in survey and _is_respondent_facing(survey[qid]):
            elements.append(_compile_question(survey[qid], by_qid))

    page: dict[str, Any] = {"name": block.block_id, "elements": elements}
    if block.label:
        page["title"] = block.label
    if block.randomize_questions:
        page["questionsOrder"] = "random"
    if block.show_if is not None:
        page["visibleIf"] = _expression_to_visibleif_string(block.show_if)
    return page


def compile_to_surveyjs(survey: SurveyDefinition) -> dict[str, Any]:
    """Compile a `SurveyDefinition` into a SurveyJS survey JSON model.

    Returns ``{"title": ..., "pages": [...]}``.

    Everything a respondent reads passes through `_for_respondent` first: a pipe
    becomes the ``{qid}`` marker SurveyJS interpolates, and every remaining
    bracketed directive is removed. The stored question keeps its original
    wording — this transformation exists only at the boundary where text stops
    being ours and starts being the public's.
    """
    pages = [_compile_block(block, survey) for block in survey.blocks]
    return {"title": survey.title, "pages": pages}


def _shuffled_choices(question: Question, choices: list[dict]) -> list[dict]:
    """Randomise the options that may move; anchored ones stay where they are."""
    movable = [i for i, opt in enumerate(question.options) if not opt.anchored]
    if len(movable) < 2 or len(choices) != len(question.options):
        return choices
    picked = [choices[i] for i in movable]
    random.shuffle(picked)
    out = list(choices)
    for slot, choice in zip(movable, picked, strict=True):
        out[slot] = choice
    return out


def display_warnings(survey: SurveyDefinition) -> list[dict[str, str]]:
    """Questions whose respondent-facing text lost something we could not resolve.

    Stripping an unresolvable directive is the right thing to serve — nobody
    should read "[PHARMACY]" — but it leaves a sentence with a hole in it, and
    that is a scripting decision a human has to make before the link goes to a
    panel. Reported rather than silently shipped.
    """
    by_qid = {q.qid: q for q in survey.questions}
    warnings: list[dict[str, str]] = []
    for qid in survey.question_order():
        if qid not in survey:
            continue
        question = survey[qid]
        if not _is_respondent_facing(question):
            continue
        techniques = _techniques(question)
        for pipe in techniques.get("pipes") or []:
            source = pipe.get("source_qid")
            verbatim = pipe.get("verbatim") or ""
            if verbatim and verbatim not in (question.text or ""):
                # Already filled in: a loop writes each brand's name where
                # "[INSERT PROVIDER]" stood, and the copied pipe record was
                # reported as a gap in a sentence that no longer has one.
                continue
            if not source or source not in by_qid:
                warnings.append(
                    {
                        "qid": qid,
                        "kind": "unresolved_pipe",
                        "detail": (
                            f"{qid} pipes in '{pipe.get('name') or pipe.get('verbatim')}', "
                            "which is not a question in this survey. The text is removed "
                            "before a respondent sees it, leaving a gap in the sentence."
                        ),
                    }
                )
        for mask in techniques.get("masks") or []:
            if mask.get("mode") == "full_list":
                continue
            if _choices_visible_if(mask, by_qid) is None:
                warnings.append(
                    {
                        "qid": qid,
                        "kind": "unenforceable_mask",
                        "detail": (
                            f"{qid} is masked from "
                            f"{mask.get('exclude_qid') or mask.get('source_qid')}, which is "
                            "not a question in this survey, so its option list cannot be "
                            "narrowed and everyone will see every option."
                        ),
                    }
                )
        if _BRACKETED.search(question.text or ""):
            resolved = _for_respondent(
                question.text, techniques.get("pipes") or [], by_qid
            )
            if not resolved:
                warnings.append(
                    {
                        "qid": qid,
                        "kind": "empty_after_stripping",
                        "detail": (
                            f"{qid}'s wording is entirely scripter directives, so a "
                            "respondent would be shown a question with no text at all."
                        ),
                    }
                )
    return warnings
