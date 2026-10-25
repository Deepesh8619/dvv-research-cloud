"""The quota plan written with the questionnaire, applied to every link opened.

A researcher thinks in shares — "half women, a third 18–34" — of a target
number of completes. Quotas were per link, set by hand on the field monitor
after the link existed. The plan lives on the questionnaire as
`metadata["quota_plan"] = {"completes": 400, "cells": [{name, qid, codes, share_pct}]}`
and each real link opened from it gets one cell per row: a limit of
share × completes, rounded up, on "the answer to qid is one of codes".

A cell is either answers to a choice question (`codes`) or a band of a number
question (`min`/`max`, inclusive, either end open): "18–34" on age.
"""

from __future__ import annotations

import math
from typing import Any

from src.core.survey_definition import Expression, QuestionType, SurveyDefinition

_CHOICE = (QuestionType.SINGLE, QuestionType.DROPDOWN)


def _band(cell: dict[str, Any]) -> tuple[float | None, float | None]:
    lo, hi = cell.get("min"), cell.get("max")
    return (float(lo) if lo not in (None, "") else None,
            float(hi) if hi not in (None, "") else None)


def problems(plan: dict[str, Any], survey: SurveyDefinition) -> list[str]:
    out = []
    completes = plan.get("completes")
    if not isinstance(completes, int) or completes < 1:
        out.append("Say how many completes the study needs.")
    totals: dict[str, float] = {}
    for c in plan.get("cells") or []:
        name, qid = c.get("name") or "a quota", c.get("qid")
        q = survey[qid] if qid in survey else None
        if q is not None and q.type is QuestionType.NUMERIC:
            try:
                lo, hi = _band(c)
            except (TypeError, ValueError):
                lo = hi = None
                out.append(f"{name}: the band's ends must be numbers.")
            if lo is None and hi is None:
                out.append(f"{name}: give the band a lower or upper end (e.g. 18 to 34).")
            elif lo is not None and hi is not None and lo > hi:
                out.append(f"{name}: the band starts above where it ends.")
        elif q is None or q.type not in _CHOICE:
            out.append(f"{name}: pick a single-choice or number question.")
            continue
        else:
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
        if c.get("codes"):
            codes = sorted(set(c["codes"]))
            condition = (Expression(op="eq", qid=c["qid"], value=codes[0]) if len(codes) == 1
                         else Expression(op="in", qid=c["qid"], values=codes))
        else:
            # Inclusive band: not below the lower end and not above the upper.
            lo, hi = _band(c)
            ends = [Expression(op="not", children=[Expression(op=op, qid=c["qid"], value=v)])
                    for op, v in (("lt", lo), ("gt", hi)) if v is not None]
            condition = ends[0] if len(ends) == 1 else Expression(op="and", children=ends)
        out.append({"name": c["name"], "condition": condition.to_dict(),
                    "limit": math.ceil(completes * c["share_pct"] / 100)})
    return out
