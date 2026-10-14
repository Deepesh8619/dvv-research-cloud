"""Explicit imputation planning. Pure: returns the cells it WOULD change, never writes.

Imputation is never silent: callers store the policy, log every cell, and the
raw data stays untouched (see cleaning.apply_cleaning).
"""

from __future__ import annotations

import statistics

import polars as pl

from src.core.models import DataMap, VarType
from src.engines.qc.cleaning import respondent_keys

METHODS = ("mean", "median", "hot_deck")
TARGETS = ("missing", "out_of_range")


def _is_routed(variable_ref: str | None, survey) -> bool:
    """True if the variable's question (or its block) has a show_if. Raises if unknowable."""
    if survey is None:
        raise ValueError("cannot impute missing values: the survey definition is unavailable, "
                         "so legitimate skip-logic nulls cannot be told from true missing")
    if not variable_ref or variable_ref not in survey:
        raise ValueError(f"cannot impute missing values: question {variable_ref!r} is not in the "
                         "survey definition, so its skip logic cannot be checked")
    if survey[variable_ref].show_if is not None:
        return True
    return any(b.show_if is not None and variable_ref in b.question_ids for b in survey.blocks)


def plan_imputation(
    df: pl.DataFrame, variable: str, method: str, target: str,
    donor_class: str | None = None, *, datamap: DataMap, survey=None,
    min: float | None = None, max: float | None = None,  # noqa: A002
) -> list[dict]:
    """Return [{key, variable, old, new}] for the cells the policy would change."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}")
    if variable not in df.columns or variable not in datamap:
        raise ValueError(f"unknown variable {variable!r}")
    if donor_class is not None and donor_class not in df.columns:
        raise ValueError(f"unknown donor_class column {donor_class!r}")
    var = datamap[variable]
    if method in ("mean", "median") and var.type in (VarType.SINGLE, VarType.MULTI):
        raise ValueError(f"{method} imputation is meaningless for {var.type.value} variable "
                         f"{variable!r} (categorical codes); use hot_deck")
    if target == "missing":
        if _is_routed(var.question_ref, survey):
            raise ValueError(f"cannot impute missing values of {variable!r}: its question has "
                             "skip logic, so nulls from routing are legitimate")
    elif min is None and max is None:
        raise ValueError("target out_of_range needs min and/or max")

    vals = df[variable].to_list()

    def bad(v) -> bool:
        if target == "missing":
            return v is None
        return v is not None and ((min is not None and v < min) or (max is not None and v > max))

    donors_ok = [v is not None and not bad(v) for v in vals]
    targets = [i for i, v in enumerate(vals) if bad(v)]
    keys = respondent_keys(df)
    classes = df[donor_class].to_list() if donor_class else [None] * len(vals)

    fill = None
    if method != "hot_deck":
        pool = [v for v, ok in zip(vals, donors_ok, strict=True) if ok]
        if not pool:
            return []
        fill = (statistics.fmean if method == "mean" else statistics.median)(pool)
        if df[variable].dtype.is_integer():
            fill = round(fill)

    last: dict = {}  # class value -> latest donor value seen so far (file order)
    target_set = set(targets)
    plan = []
    for i, v in enumerate(vals):
        if i in target_set:
            new = fill if method != "hot_deck" else last.get(classes[i])
            if new is not None:
                plan.append({"key": keys[i], "variable": variable, "old": v, "new": new})
        if donors_ok[i]:
            last[classes[i]] = v
    return plan
