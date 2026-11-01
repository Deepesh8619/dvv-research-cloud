"""Groups an analyst would build from a "create … variable" note.

- the groups written after the ':' of the note itself, run together or comma-separated;
- two lists in one note (a list that restarts), kept apart;
- nested groups ("M18-34", "Female 65+"): gender code AND age range, with the
  gender and age questions found by their answers and wording, wherever they sit.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.survey_definition import Expression, SurveyDefinition
from src.engines.questionnaire.conditions import compile_condition
from src.engines.questionnaire.note_lines import split_groups

_RANGE = re.compile(r"^\s*(\d{1,3})\s*[-–]\s*(\d{1,3})\s*$|^\s*(\d{1,3})\s*\+\s*$")
NESTED = re.compile(r"^\s*(male|female|m|f)\s*(\d{1,3})\s*(?:[-–]\s*(\d{1,3})|(\+))\s*$", re.I)


def with_reask(expr: Expression, survey: SurveyDefinition) -> Expression:
    """Count the second ask too: a test on S1 becomes
    (test on S1 and S1 != opt-out) or (the same test on S1_REASK).

    S1_REASK is only answered by those who gave the opt-out, so it must not
    replace S1 (most respondents never see it)."""
    again = {r["qid"]: r for r in survey.metadata.get("note_flows", {}).get("reask", [])}
    if not again:
        return expr
    if expr.children is not None:
        return Expression(op=expr.op, children=[with_reask(c, survey) for c in expr.children],
                          verbatim_text=expr.verbatim_text)
    flow = again.get(expr.qid or "")
    if flow is None:
        return expr
    second = Expression.from_dict({**expr.to_dict(), "qid": flow["reask"]})
    first = Expression(op="and", children=[
        expr, Expression(op="ne", qid=expr.qid, value=flow["opt_out"])])
    return Expression(op="or", children=[first, second], verbatim_text=expr.verbatim_text)


def band_lines(rest: str) -> list[str]:
    """The groups written after the ':' in the note itself, one per item."""
    _, sep, tail = rest.partition(":")
    if not sep or not tail.strip():
        return []
    parts = [p.strip() for p in tail.split(",") if p.strip()] if "," in tail \
        else split_groups(tail)
    if len(parts) > 1 and all(_RANGE.match(p) or NESTED.match(p) for p in parts):
        return parts
    return []


def split_lists(lines: list[str]) -> list[list[str]]:
    """One list, or several when an age range starts again at a low value already used."""
    lists: list[list[str]] = [[]]
    lows: set[str] = set()
    for line in lines:
        m = _RANGE.match(line)
        low = (m.group(1) or m.group(3)) if m else None
        if low is not None and (low in lows or line in lists[-1]):
            lists.append([])
            lows = set()
        if low is not None:
            lows.add(low)
        lists[-1].append(line)
    return lists


def _gender(survey: SurveyDefinition):
    found = []
    for q in survey.questions:
        labels = {o.label.strip().lower(): o.code for o in q.options or []}
        if q.type.value == "single" and "male" in labels and "female" in labels:
            found.append((q.qid, labels["male"], labels["female"]))
    return found


def _age(host, survey: SurveyDefinition) -> list[str]:
    if host.type.value == "numeric":
        return [host.qid]
    return [q.qid for q in survey.questions
            if q.type.value == "numeric" and re.search(r"\bage\b", q.text or "", re.I)]


def nested_variable(var: dict[str, Any], lines: list[str], host, survey) -> dict[str, Any]:
    """Fill `var` with gender-and-age bands, or say which question is missing."""
    genders, ages = _gender(survey), _age(host, survey)
    for found, what in ((genders, "gender"), (ages, "age")):
        if len(found) != 1:
            why = f"the {what} question is " + ("missing" if not found else "ambiguous")
            var.update(status="needs_you", why=why, missing=f"{what} question")
            return var
    (g, male, female), age = genders[0], ages[0]
    for line in lines:
        m = NESTED.match(line)
        band = {"line": line, "label": line.strip(), "condition": None}
        if not m:
            band["unread"] = "not a gender-and-age group"
        else:
            code = male if m.group(1).lower().startswith("m") and m.group(1).lower() != "female" \
                else female
            span = (f"{age} between {m.group(2)} and {m.group(3)}" if m.group(3)
                    else f"{age} >= {m.group(2)}")
            try:
                band["condition"] = with_reask(
                    compile_condition(f"{g} = {code} AND {span}", survey), survey).to_dict()
            except ValueError as exc:
                band["unread"] = str(exc)
        var["bands"].append(band)
    if any(b["condition"] is None for b in var["bands"]):
        var.update(status="needs_you", why="some gender-and-age groups have no exact rule")
    return var
