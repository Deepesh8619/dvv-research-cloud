"""Advanced methods on a study's delivered data: MaxDiff, TURF, Van Westendorp, drivers.

Completes only, the same rows the tables are computed on. Aggregates only —
nothing respondent-level leaves — so the gate is the one the tables use.
"""

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user, require_role
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


# --- weighting -------------------------------------------------------------------

WEIGHT_VARIABLE = "dvv_weight"


class WeightingRequest(BaseModel):
    #: variable -> code -> share (percentages or proportions; normalised per variable)
    targets: dict[str, dict[str, float]] = Field(min_length=1)
    cap_low: float = Field(default=0.3, gt=0, le=1)
    cap_high: float = Field(default=3.0, ge=1, le=20)


def _weighting(study) -> dict | None:
    return (study.study_metadata or {}).get("weighting")


def _rake_study(db: Session, study_id: str, body: WeightingRequest):
    """Rake this study's data to the targets: (result, parquet, datamap path, targets)."""
    import polars as pl

    from src.core.models import DataMap
    from src.engines.analysis.weighting import rake

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    parquet, dm_path = _run_artifacts(study, f"Study {study_id}")
    dm = DataMap.from_json(dm_path.read_text())
    targets: dict[str, dict[int, float]] = {}
    for var, cells in body.targets.items():
        if var not in dm:
            raise HTTPException(status_code=422, detail=f"{var} is not a variable in the data")
        codes = set(dm[var].valid_codes())
        try:
            parsed = {int(k): float(v) for k, v in cells.items()}
        except ValueError:
            raise HTTPException(status_code=422, detail=(
                f"{var}: targets are code -> share, e.g. {{\"1\": 49}}")) from None
        if bad := sorted(set(parsed) - codes):
            raise HTTPException(status_code=422, detail=(
                f"{var} has no code(s) {bad}; its codes are " + ", ".join(
                    f"{c} {dm[var].values.get(c, '')}".strip() for c in sorted(codes))))
        targets[var] = parsed
    frame = pl.read_parquet(parquet, columns=list(targets))
    columns = {v: frame[v].cast(pl.Float64).to_numpy() for v in targets}
    try:
        result = rake(columns, targets, cap_low=body.cap_low, cap_high=body.cap_high)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    labels = {v: {c: dm[v].values.get(c, str(c)) for c in targets[v]} for v in targets}
    report = result.report()
    for v, cells in report["targets"].items():
        for code, row in cells.items():
            row["label"] = labels[v].get(code, str(code))
    return result, report, parquet, dm_path, targets


@router.get("/studies/{study_id}/weighting")
def weighting_status(study_id: str, db: Session = Depends(get_db),
                     user: UserDB = Depends(get_current_user)):
    """The applied weighting, its quality, and whether the data has changed since."""
    import polars as pl

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    scheme = _weighting(study)
    candidates = _weighting_candidates(study)
    if not scheme:
        return {"applied": False, "weight_variable": None, "candidates": candidates}
    stale = None
    try:
        parquet, _dm = _run_artifacts(study, f"Study {study_id}")
        n = pl.scan_parquet(parquet).select(pl.len()).collect().item()
        cols = pl.read_parquet_schema(parquet)
        if WEIGHT_VARIABLE not in cols:
            stale = "the data was rebuilt without the weight column — apply the weighting again"
        elif n != scheme["report"]["n"]:
            stale = (f"the data now has {n} respondents, the weights were made for "
                     f"{scheme['report']['n']} — apply the weighting again")
    except HTTPException:
        stale = "the data file is not on disk"
    return {"applied": True, "weight_variable": WEIGHT_VARIABLE, "stale": stale,
            "candidates": candidates, **scheme}


def _weighting_candidates(study) -> list[dict]:
    """Variables a sample is usually weighted on: the likely banners first (gender,
    age band, region), then any other single-choice variable with 2-12 categories."""
    from api.routes.chat import _load_datamap, _study_dir
    from src.orchestration.orchestrator import suggest_banner_candidates

    dm = _load_datamap(_study_dir(study), study)
    if dm is None:
        return []
    first = suggest_banner_candidates(dm, 8)
    rest = [v.name for v in dm.variables if v.type.value == "single"
            and 2 <= len(v.valid_codes()) <= 12 and v.name not in first]
    return [{"name": n, "label": dm[n].label or n,
             "codes": [{"code": c, "label": dm[n].values.get(c, str(c))}
                       for c in dm[n].valid_codes()]}
            for n in [*first, *rest][:80]]


@router.post("/studies/{study_id}/weighting/preview")
def weighting_preview(study_id: str, body: WeightingRequest, db: Session = Depends(get_db),
                      user: UserDB = Depends(get_current_user)):
    """What these targets would do — efficiency, extremes, achieved shares — saving nothing."""
    _result, report, *_ = _rake_study(db, study_id, body)
    return report


@router.post("/studies/{study_id}/weighting")
def apply_weighting(study_id: str, body: WeightingRequest, db: Session = Depends(get_db),
                    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST))):
    """Rake to the targets and write the weight into the data as dvv_weight. (Owner/Analyst)"""
    import os
    from datetime import datetime

    import polars as pl

    from api.db import update_study_paths
    from api.routes.studies import _set_meta
    from src.core.models import DataMap, Variable, VarType

    result, report, parquet, dm_path, targets = _rake_study(db, study_id, body)
    frame = pl.read_parquet(parquet).with_columns(pl.Series(WEIGHT_VARIABLE, result.weights))
    tmp = parquet.with_suffix(".parquet.tmp")
    frame.write_parquet(tmp)
    os.replace(tmp, parquet)  # atomic: never a half-written data file
    dm = DataMap.from_json(dm_path.read_text())
    label = "Weight (rim: " + ", ".join(targets) + ")"
    if WEIGHT_VARIABLE in dm:
        dm[WEIGHT_VARIABLE].label = label
    else:
        dm.add(Variable(name=WEIGHT_VARIABLE, label=label, type=VarType.NUMERIC,
                        provenance="weighting"))
    dm_path.write_text(dm.to_json())
    update_study_paths(db, study_id, datamap_json=dm.to_json())
    scheme = {"targets": {v: {str(c): s for c, s in cells.items()} for v, cells in targets.items()},
              "cap_low": body.cap_low, "cap_high": body.cap_high, "report": report,
              "applied_by": user.name, "applied_at": datetime.now().isoformat(timespec="seconds")}
    _set_meta(db, study_id, weighting=scheme)
    return {"applied": True, "weight_variable": WEIGHT_VARIABLE, **scheme}


@router.delete("/studies/{study_id}/weighting", status_code=204)
def remove_weighting(study_id: str, db: Session = Depends(get_db),
                     user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST))):
    import os

    import polars as pl

    from api.db import update_study_paths
    from api.routes.studies import _set_meta
    from src.core.models import DataMap

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    parquet, dm_path = _run_artifacts(study, f"Study {study_id}")
    frame = pl.read_parquet(parquet)
    if WEIGHT_VARIABLE in frame.columns:
        tmp = parquet.with_suffix(".parquet.tmp")
        frame.drop(WEIGHT_VARIABLE).write_parquet(tmp)
        os.replace(tmp, parquet)
    dm = DataMap.from_json(dm_path.read_text())
    if WEIGHT_VARIABLE in dm:
        dm = DataMap([v for v in dm.variables if v.name != WEIGHT_VARIABLE])
        dm_path.write_text(dm.to_json())
        update_study_paths(db, study_id, datamap_json=dm.to_json())
    _set_meta(db, study_id, weighting=None)
