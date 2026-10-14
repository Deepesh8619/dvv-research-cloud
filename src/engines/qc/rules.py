"""Declared quality rules: trap questions, fake brands, contradictions, ranges.

Rules are declared by a human (or proposed from the questionnaire's own
validation limits), NEVER inferred from data: none of the real questionnaires
contains a trap question, so auto-detection cannot be measured. With no rules
declared there are zero flags. A rule naming a column that does not exist
would silently never fire, so `validate_rules` refuses it instead.

Rule kinds (all over variable names):
  trap            {variable, pass_codes}      answered and not a pass code
  red_herring     {variables} | {variable, codes}   fake brand selected
  flag_if         {expr, why}                 Expression dict from survey_definition
  age_birth_year  {age, birth_year, tolerance=1, field_year?}
  range           {variable, min, max}

Honest skip: a row with a null/non-numeric value in a referenced column is
skipped, never flagged.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import polars as pl

from src.core.models import DataMap
from src.core.survey_definition import Expression, QuestionType, SurveyDefinition, evaluate

KINDS = {
    "trap": "trap_failed",
    "red_herring": "red_herring",
    "flag_if": "inconsistent",
    "age_birth_year": "inconsistent",
    "range": "out_of_range",
}
_START = "vStartTime"


def _expr_vars(raw: dict[str, Any]) -> set[str]:
    return Expression.from_dict(raw).referenced_qids()


def _variables(rule: dict[str, Any]) -> list[str]:
    kind = rule.get("kind")
    if kind in ("trap", "range"):
        return [rule.get("variable")]
    if kind == "red_herring":
        return list(rule.get("variables") or [rule.get("variable")])
    if kind == "age_birth_year":
        return [rule.get("age"), rule.get("birth_year")] + (
            [rule["field_year"]] if isinstance(rule.get("field_year"), str) else []
        )
    if kind == "flag_if":
        return sorted(_expr_vars(rule.get("expr") or {}))
    return []


_REQUIRED = {
    "trap": ("variable", "pass_codes"),
    "flag_if": ("expr",),
    "age_birth_year": ("age", "birth_year"),
    "range": ("variable",),
}


def _missing(rule: dict[str, Any]) -> list[str]:
    """Required fields the rule lacks (empty list = well-formed)."""
    kind = rule.get("kind")
    miss = [f for f in _REQUIRED.get(kind, ()) if rule.get(f) is None]
    if kind == "range" and rule.get("min") is None and rule.get("max") is None:
        miss.append("min or max")
    if kind == "red_herring" and not (
        rule.get("variables") or (rule.get("variable") and rule.get("codes"))
    ):
        miss.append("variables, or variable and codes")
    return miss


def validate_rules(rules: list[dict[str, Any]], datamap: DataMap) -> list[str]:
    """Problems that would make a rule wrong or silent; empty list = usable."""
    problems: list[str] = []
    for i, rule in enumerate(rules):
        kind = rule.get("kind")
        if kind not in KINDS:
            problems.append(f"rule {i}: unknown kind {kind!r}")
            continue
        miss = _missing(rule)
        if miss:
            problems.append(f"rule {i} ({kind}): missing required field {', '.join(miss)}")
            continue
        try:
            names = _variables(rule)
        except (KeyError, TypeError, AttributeError):
            problems.append(f"rule {i} ({kind}): malformed rule")
            continue
        for name in names:
            if name not in datamap:
                problems.append(f"rule {i} ({kind}): unknown variable {name!r}")
    return problems


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _year(v: Any) -> int | None:
    if isinstance(v, datetime):
        return v.year
    n = _num(v)
    return int(n) if n is not None else None


def run_rules(
    df: pl.DataFrame, rules: list[dict[str, Any]], key_of: Callable[[int], Any]
) -> list[dict[str, Any]]:
    """Flags as [{key, check, detail}]. `key_of(row_index)` names a respondent.

    Rules that fail `validate_rules`-style checks (missing column) are skipped.
    """
    flags: list[dict[str, Any]] = []
    cols = set(df.columns)
    for rule in rules:
        kind = rule.get("kind")
        if kind not in KINDS or _missing(rule):
            continue
        try:
            names = _variables(rule)
        except (KeyError, TypeError, AttributeError):
            continue
        if not names or any(n not in cols for n in names):
            continue
        check = KINDS[kind]
        if kind == "age_birth_year" and rule.get("field_year") is None:
            if _START not in cols:
                continue  # honest skip: no year to compare against
        for i, row in enumerate(df.iter_rows(named=True)):
            detail = _check_row(kind, rule, row)
            if detail:
                flags.append({"key": key_of(i), "check": check, "detail": detail})
    return flags


def _check_row(kind: str, rule: dict[str, Any], row: dict[str, Any]) -> str | None:
    if kind == "trap":
        v = row[rule["variable"]]
        if v is None or v in (rule.get("pass_codes") or []):
            return None
        return f"{rule['variable']}={v!r}, expected one of {rule.get('pass_codes')}"
    if kind == "red_herring":
        if rule.get("variables"):
            hit = [n for n in rule["variables"] if _num(row[n]) == 1]
        else:
            v = row[rule["variable"]]
            hit = [rule["variable"]] if v is not None and v in (rule.get("codes") or []) else []
        return f"selected fake option: {', '.join(hit)}" if hit else None
    if kind == "range":
        n = _num(row[rule["variable"]])
        lo, hi = rule.get("min"), rule.get("max")
        if n is None or ((lo is None or n >= lo) and (hi is None or n <= hi)):
            return None
        return f"{rule['variable']}={n:g} outside {lo}..{hi}"
    if kind == "age_birth_year":
        age, born = _num(row[rule["age"]]), _num(row[rule["birth_year"]])
        fy = rule.get("field_year")
        year = fy if isinstance(fy, int) else _year(row[fy] if fy else row[_START])
        if age is None or born is None or year is None:
            return None
        tol = rule.get("tolerance", 1)
        if abs((year - born) - age) <= tol:
            return None
        return f"age {age:g} vs birth year {born:g} in {year} (tolerance {tol})"
    # flag_if
    expr = Expression.from_dict(rule["expr"])
    answers = {n: row[n] for n in expr.referenced_qids()}
    if any(v is None for v in answers.values()):
        return None
    return rule.get("why") or "flag_if condition met" if evaluate(expr, answers) else None


def rules_from_survey(survey: SurveyDefinition) -> list[dict[str, Any]]:
    """Propose `range` rules from numeric questions' declared min/max.

    Assumes the data column is named after the qid; `validate_rules` against the
    datamap drops/reports the ones that do not exist.
    """
    out = []
    for q in survey.questions:
        v = q.validation or {}
        numeric = q.type == QuestionType.NUMERIC
        if numeric and _num(v.get("min")) is not None and _num(v.get("max")) is not None:
            out.append({"kind": "range", "variable": q.qid, "min": v["min"], "max": v["max"]})
    return out
