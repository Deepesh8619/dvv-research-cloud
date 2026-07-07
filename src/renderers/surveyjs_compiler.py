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

from typing import Any

from src.core.survey_definition import (
    Block,
    Expression,
    Question,
    QuestionType,
    SurveyDefinition,
)

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

    ref = f"{{{expr.qid}}}"

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


def _choices(options: list) -> list[dict[str, Any]]:
    return [{"value": o.code, "text": o.label} for o in options]


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


def _compile_question(question: Question) -> dict[str, Any]:
    """Compile one `Question` into its SurveyJS element dict."""
    qtype = question.type

    # Info content carries no answer — no `name`, no `isRequired`.
    if qtype == QuestionType.INFO:
        return {"type": "html", "html": question.text}

    element: dict[str, Any] = {"name": question.qid, "title": question.text}

    if qtype == QuestionType.SINGLE:
        element["type"] = "radiogroup"
        element["choices"] = _choices(question.options)
    elif qtype == QuestionType.MULTI:
        element["type"] = "checkbox"
        element["choices"] = _choices(question.options)
    elif qtype == QuestionType.DROPDOWN:
        element["type"] = "dropdown"
        element["choices"] = _choices(question.options)
    elif qtype == QuestionType.RANK:
        element["type"] = "ranking"
        element["choices"] = _choices(question.options)
    elif qtype == QuestionType.GRID_SINGLE:
        element["type"] = "matrix"
        element["columns"] = _choices(question.cols or [])
        element["rows"] = _choices(question.rows or [])
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
        element["choicesOrder"] = "random"

    if question.show_if is not None:
        element["visibleIf"] = _expression_to_visibleif_string(question.show_if)

    return element


def _compile_block(block: Block, survey: SurveyDefinition) -> dict[str, Any]:
    """Compile one `Block` into a SurveyJS page dict."""
    elements: list[dict[str, Any]] = []
    for qid in block.question_ids:
        if qid in survey:
            elements.append(_compile_question(survey[qid]))

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

    Returns ``{"title": ..., "pages": [...]}``. Piping is preserved implicitly:
    SurveyJS interpolates ``{qid}`` markers already present in question text at
    runtime, so no transformation is needed on our side (`piping_refs` is
    provenance for our own tooling, not something SurveyJS reads).
    """
    pages = [_compile_block(block, survey) for block in survey.blocks]
    return {"title": survey.title, "pages": pages}
