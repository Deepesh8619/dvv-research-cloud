"""The quota plan written with the questionnaire, applied to every link opened.

A researcher thinks in shares — "half women, a third 18–34" — of a target
number of completes. Quotas were per link, set by hand on the field monitor
after the link existed. The plan lives on the questionnaire as
`metadata["quota_plan"] = {"completes": 400, "cells": [{name, qid, codes, share_pct}]}`
and each real link opened from it gets one cell per row: a limit of
share × completes, rounded up, on "the answer to qid is one of codes".

Only choice questions (single / dropdown) — a cell is an answer someone gave.
"""

from __future__ import annotations

import math
from typing import Any

from src.core.survey_definition import Expression, QuestionType, SurveyDefinition

_CHOICE = (QuestionType.SINGLE, QuestionType.DROPDOWN)


def problems(plan: dict[str, Any], survey: SurveyDefinition) -> list[str]:
    out = []
    completes = plan.get("completes")
    if not isinstance(completes, int) or completes < 1:
        out.append("Say how many completes the study needs.")
    totals: dict[str, float] = {}
    for c in plan.get("cells") or []:
        name, qid = c.get("name") or "a quota", c.get("qid")
        q = survey[qid] if qid in survey else None
        if q is None or q.type not in _CHOICE:
            out.append(f"{name}: pick a single-choice question.")
            continue
        codes = set(c.get("codes") or [])
        if not codes or not codes <= {o.code for o in q.options}:
            out.append(f"{name}: pick answers of {qid}.")
        share = c.get("share_pct")
        if not isinstance(share, int | float) or not 0 < share <= 100:
            out.append(f"{name}: the share must be between 1 and 100%.")
            continue
        totals[qid] = totals.get(qid, 0) + share
    out += [f"{qid}: shares add up to {total:g}%, more than 100%."
            for qid, total in totals.items() if total > 100.0001]
    return out


def cells(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Quota cells for a link: name, condition, limit."""
    completes = plan["completes"]
    out = []
    for c in plan.get("cells") or []:
        codes = sorted(set(c["codes"]))
        condition = (Expression(op="eq", qid=c["qid"], value=codes[0]) if len(codes) == 1
                     else Expression(op="in", qid=c["qid"], values=codes))
        out.append({"name": c["name"], "condition": condition.to_dict(),
                    "limit": math.ceil(completes * c["share_pct"] / 100)})
    return out
