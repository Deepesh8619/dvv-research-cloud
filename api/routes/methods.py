"""Advanced methods on a study's delivered data: MaxDiff, TURF, Van Westendorp, drivers.

Completes only, the same rows the tables are computed on. Aggregates only —
nothing respondent-level leaves — so the gate is the one the tables use.
"""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.db import UserDB, get_db, get_study
from api.routes.studies import _run_artifacts

router = APIRouter()

# A rating scale point: "4", "1 - Strongly disagree", "10: Extremely likely"
# (not an age band "13-17"), or the words scales are written in.
_POINT = re.compile(r"^\d{1,2}(\s*[-–:.]\s*[^\d\s].*)?$")
_SCALE_WORDS = re.compile(
    r"\b(agree|disagree|satisf\w*|dissatisf\w*|likely|unlikely|important|excellent|poor|good|"
    r"bad|very|extremely|somewhat|not at all|completely|trust\w*|recommend|appeal\w*)\b", re.I)


def _is_scale(values: dict[int, str]) -> bool:
    """A question someone rates on (4 to 11 points), not gender, age bands or income."""
    labels = [str(lab) for code, lab in values.items() if code < 90]
    if not 4 <= len(labels) <= 11:
        return False
    points = sum(bool(_POINT.match(lab.strip())) for lab in labels)
    worded = sum(bool(_SCALE_WORDS.search(lab)) for lab in labels)
    return points * 2 >= len(labels) or worded * 2 >= len(labels)


def _data(db: Session, study_id: str):
    import polars as pl

    from src.core.models import DataMap
    from src.engines.qc.discrepancy import completes_only

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    parquet, datamap = _run_artifacts(study, f"Study {study_id}")
    dm = DataMap.from_json(datamap.read_text())
    df, _fielded = completes_only(pl.read_parquet(parquet), dm)
    return df, dm


@router.get("/studies/{study_id}/methods")
def method_inputs(study_id: str, db: Session = Depends(get_db),
                  user: UserDB = Depends(get_current_user)):
    """What each method can run on in this file. (Authenticated)"""
    from src.core.models import VarType
    from src.engines.analysis.methods import maxdiff_exercises, option_columns
    from src.engines.qc.variable_roles import classify_variable

    df, dm = _data(db, study_id)
    shown = [v for v in dm.variables if v.name in df.columns
             and classify_variable(v.name, v.label or "", v.question_ref) in ("question", "punch")]
    groups: dict[str, str] = {}
    for v in shown:
        if (m := re.match(r"^(.+?)r\d+$", v.name)) and m[1] not in groups:
            cols = option_columns(df, m[1])
            if len(cols) >= 2 and set(df[cols[0]].drop_nulls().unique().to_list()) <= {0, 1}:
                groups[m[1]] = (v.question_text or m[1])[:120]
    return {
        "n_respondents": df.height,
        "maxdiff": [{"exercise": q, "screens": n} for q, n in maxdiff_exercises(df).items()],
        "multi_select": [{"question": q, "text": t} for q, t in groups.items()],
        "numeric": [{"variable": v.name, "label": (v.label or v.name)[:120]}
                    for v in shown if v.type == VarType.NUMERIC],
        "ratings": [{"variable": v.name, "label": (v.label or v.name)[:120]}
                    for v in shown if v.type == VarType.SINGLE and _is_scale(v.values)],
    }


class MethodRequest(BaseModel):
    method: Literal["maxdiff", "turf", "van_westendorp", "drivers"]
    exercise: str | None = None
    question: str | None = None
    max_size: int = Field(default=5, ge=1, le=8)
    prices: list[str] | None = None  # too cheap, cheap, expensive, too expensive
    outcome: str | None = None
    predictors: list[str] | None = None


@router.post("/studies/{study_id}/methods")
def run_method(study_id: str, body: MethodRequest, db: Session = Depends(get_db),
               user: UserDB = Depends(get_current_user)):
    """Run one method; a request the data cannot answer is a 422 saying why. (Authenticated)"""
    from src.engines.analysis import methods

    df, dm = _data(db, study_id)
    missing = [c for c in [*(body.prices or []), body.outcome, *(body.predictors or [])]
               if c and c not in df.columns]
    if missing:
        raise HTTPException(status_code=422, detail=f"Not in the data: {', '.join(missing)}")
    try:
        if body.method == "maxdiff":
            return methods.maxdiff_scores(df, dm, body.exercise or "")
        if body.method == "turf":
            return methods.turf(df, dm, methods.option_columns(df, body.question or ""),
                                body.max_size)
        if body.method == "van_westendorp":
            if len(body.prices or []) != 4:
                raise ValueError("give the four price questions: too cheap, cheap, "
                                 "expensive, too expensive")
            return methods.van_westendorp(df, *body.prices)
        if not body.outcome or len(body.predictors or []) < 2:
            raise ValueError("give an outcome and at least two attributes")
        return methods.drivers(df, dm, body.outcome, body.predictors)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
