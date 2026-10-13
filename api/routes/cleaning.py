"""Respondent cleaning review: flags, human remove/keep decisions, audit trail.

Every flag needs a person: `keep` takes exactly one key (a flagged respondent
is never bulk-approved); `remove` may be bulk under one reason. Decisions are
append-only audit rows (entity "cleaning.respondent"), latest per key wins.
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user, require_role
from api.config import STUDIES_DIR
from api.db import AuditLogDB, UserDB, get_db, get_study, record_audit
from api.heavy import run_heavy_detached
from api.routes.materialize import data_access
from api.routes.studies import _run_artifacts

router = APIRouter()
ENTITY = "cleaning.respondent"
_DEFAULT = {"rules": [], "imputations": [], "locked": False, "versions": []}


class DecisionRequest(BaseModel):
    keys: list[str]
    decision: Literal["remove", "keep"]
    reason_code: str
    note: str | None = None


def _flags_job(parquet: Path, datamap: Path, rules: list[dict]):
    """Top-level so it can run in the process pool; loads its own data."""
    import polars as pl

    from src.core.models import DataMap
    from src.engines.qc.cleaning import collect_flags, respondent_key_column, respondent_keys

    df = pl.read_parquet(parquet)
    flags, skipped = collect_flags(df, DataMap.from_json(datamap.read_text()), rules)
    return respondent_key_column(df), respondent_keys(df), flags, skipped


def _cleaning(study) -> dict:
    return {**_DEFAULT, **((study.study_metadata or {}).get("cleaning") or {})}


def _decisions(db: Session, study_id: str) -> dict[str, dict]:
    from src.engines.qc.cleaning import current_decisions

    rows = (
        db.query(AuditLogDB)
        .filter(AuditLogDB.entity == ENTITY, AuditLogDB.study_id == study_id)
        .order_by(AuditLogDB.at.asc())
        .all()
    )
    return current_decisions([
        {"entity_id": r.entity_id, "action": r.action,
         "after": json.loads(r.after_json) if r.after_json else None}
        for r in rows
    ])


@router.get("/studies/{study_id}/cleaning")
def get_cleaning(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Flags per respondent plus the decisions so far. Gated like a download."""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    access = data_access(db, study_id, user)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)
    parquet, datamap = _run_artifacts(study, f"Study {study_id}")
    state = _cleaning(study)
    decisions = _decisions(db, study_id)
    key_column, _keys, flags, skipped = run_heavy_detached(
        db, _flags_job, parquet, datamap, state["rules"]
    )
    return {
        "key_column": key_column,
        "flags": flags,
        "skipped": skipped,
        "decisions": decisions,
        "n_unreviewed_flagged": sum(1 for k in flags if k not in decisions),
        "locked": bool(state["locked"]),
        "versions": state["versions"],
        "rules": state["rules"],
        "imputations": state["imputations"],
    }


@router.post("/studies/{study_id}/cleaning/decisions")
def post_decisions(
    study_id: str,
    body: DecisionRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    from src.engines.qc.cleaning import REASON_CODES, respondent_keys

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    if _cleaning(study)["locked"]:
        raise HTTPException(status_code=409, detail="Cleaning is locked for this study")
    if body.reason_code not in REASON_CODES:
        raise HTTPException(status_code=422, detail=f"reason_code must be one of {REASON_CODES}")
    if body.reason_code == "other" and not (body.note or "").strip():
        raise HTTPException(status_code=422, detail="A note is required for reason 'other'")
    if not body.keys:
        raise HTTPException(status_code=422, detail="No respondent keys given")
    if body.decision == "keep" and len(set(body.keys)) != 1:
        raise HTTPException(
            status_code=422, detail="keep takes exactly one respondent; review flags one by one"
        )
    import polars as pl

    parquet, datamap = _run_artifacts(study, f"Study {study_id}")
    # ponytail: reads the whole parquet to get keys; cache keys if this gets hot.
    known = set(respondent_keys(pl.read_parquet(parquet)))
    unknown = sorted(set(body.keys) - known)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown respondent keys: {unknown[:5]}")
    for key in dict.fromkeys(body.keys):
        record_audit(
            db, entity=ENTITY, entity_id=key, action=body.decision, actor_id=user.id,
            study_id=study_id, after={"reason_code": body.reason_code, "note": body.note},
        )
    db.commit()
    return {"recorded": len(set(body.keys)), "decision": body.decision}


class UnlockRequest(BaseModel):
    note: str


def _save_cleaning(db: Session, study_id: str, state: dict) -> None:
    study = get_study(db, study_id)  # fresh: _publish_tables commits and refreshes
    study.study_metadata = {**(study.study_metadata or {}), "cleaning": state}  # new dict
    db.commit()


def _survey_or_none(db: Session, study_id: str):
    from api.routes import materialize

    try:
        return materialize._approved_survey(db, study_id)
    except Exception:  # no approved/readable questionnaire: imputation of missing is refused
        return None


def _resolve_imputations(db, study_id, run_dir, datamap_path, policies) -> list[dict]:
    """Stored policies -> [{key, variable, old, new, method, reason}], planned on the raw frame."""
    if not policies:
        return []
    from src.core.models import DataMap
    from src.engines.qc.impute import plan_imputation

    raw = run_dir / "data_raw.parquet"
    df = pl_read(raw if raw.exists() else run_dir / "data.parquet")
    datamap = DataMap.from_json(datamap_path.read_text())
    survey = _survey_or_none(db, study_id)
    cells = []
    for p in policies:
        try:
            plan = plan_imputation(
                df, p["variable"], p["method"], p["target"], p.get("donor_class"),
                datamap=datamap, survey=survey, min=p.get("min"), max=p.get("max"),
            )
        except ValueError as e:
            raise HTTPException(
                status_code=409, detail=f"Imputation policy no longer valid: {e}"
            ) from e
        cells += [{**c, "method": p["method"], "reason": p["target"], "by": p.get("decided_by"),
                   "at": p.get("decided_at")} for c in plan]
    return cells


def pl_read(path: Path):
    import polars as pl

    return pl.read_parquet(path)


def _write_cleaning_log(path, removed, decisions, cells, policies, by, at) -> None:
    """One row per removal and per imputed cell: the record of what the data no longer shows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["key", "variable", "old", "new", "method", "reason_code", "decided_by",
                    "decided_at"])
        for k, reason in removed.items():
            w.writerow([k, "", "", "", "remove", reason, by, at])
        for c in cells:
            w.writerow([c["key"], c["variable"], c["old"], c["new"], c["method"], c["reason"],
                        c["by"] or by, c["at"] or at])


@router.post("/studies/{study_id}/cleaning/lock")
def lock_cleaning(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Freeze the cleaning: drop removed rows from the tables, keep them in the delivery."""
    import polars as pl

    from api.routes.materialize import _publish_tables, _write_labelled_xlsx
    from src.adapters.spss import write_sav
    from src.core.models import DataMap
    from src.engines.qc.cleaning import annotate_delivery, apply_cleaning

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    state = _cleaning(study)
    if state["locked"]:
        raise HTTPException(status_code=409, detail="Cleaning is already locked")
    parquet, datamap_path = _run_artifacts(study, f"Study {study_id}")
    run_dir = parquet.parent
    decisions = _decisions(db, study_id)
    _, _, flags, _ = run_heavy_detached(db, _flags_job, parquet, datamap_path, state["rules"])
    unreviewed = sum(1 for k in flags if k not in decisions)
    if unreviewed:
        raise HTTPException(
            status_code=409,
            detail=f"{unreviewed} flagged respondent(s) have no decision; review every flag first",
        )
    # ponytail: no client tab-plan detector. Nothing in api/ or the study row records
    # a tab_plan_file, so a custom plan is replaced by auto_tab_plan on lock.
    removed = {k: d["reason_code"] for k, d in decisions.items() if d["decision"] == "remove"}
    cells = _resolve_imputations(db, study_id, run_dir, datamap_path, state["imputations"])
    cells = [c for c in cells if c["key"] not in removed]  # a removed row needs no fill
    counts = apply_cleaning(run_dir, removed, cells)
    datamap = DataMap.from_json(datamap_path.read_text())
    _publish_tables(db, study, run_dir, datamap)

    fielded = bool((study.study_metadata or {}).get("materialized_from_link"))
    if not fielded:  # fielded studies already carry every-disposition delivery files
        out_dir = STUDIES_DIR / study_id  # where download/{spss,responses} look
        out_dir.mkdir(parents=True, exist_ok=True)
        raw, dm = annotate_delivery(pl.read_parquet(run_dir / "data_raw.parquet"), datamap, removed)
        pdf = raw.to_pandas()
        write_sav(pdf, dm, str(out_dir / f"{study_id}_responses.sav"))
        _write_labelled_xlsx(pdf, dm, out_dir / f"{study_id}_responses.xlsx")

    version = {
        "version": len(state["versions"]) + 1, "removed": removed,
        "imputations": cells, "locked_by": user.name,
        "locked_at": datetime.now().isoformat(), **counts,
    }
    _write_cleaning_log(
        STUDIES_DIR / study_id / f"{study_id}_cleaning_log.csv", removed, decisions, cells,
        state["imputations"], user.name, version["locked_at"],
    )
    _save_cleaning(db, study_id, {
        **state, "locked": True, "versions": [*state["versions"], version],
    })
    record_audit(
        db, entity="cleaning.lock", entity_id=study_id, action="lock", actor_id=user.id,
        study_id=study_id, after={"version": version["version"], **counts},
    )
    db.commit()
    return version


@router.post("/studies/{study_id}/cleaning/unlock")
def unlock_cleaning(
    study_id: str,
    body: UnlockRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER)),
):
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    if not body.note.strip():
        raise HTTPException(status_code=422, detail="A note is required to unlock")
    state = _cleaning(study)
    if not state["locked"]:
        raise HTTPException(status_code=409, detail="Cleaning is not locked")
    _save_cleaning(db, study_id, {**state, "locked": False})
    record_audit(
        db, entity="cleaning.lock", entity_id=study_id, action="unlock", actor_id=user.id,
        study_id=study_id, after={"note": body.note},
    )
    db.commit()
    return {"locked": False}


class RulesRequest(BaseModel):
    rules: list[dict]


@router.get("/studies/{study_id}/cleaning/rules")
def get_rules(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Saved rules, plus rules PROPOSED from the survey: shown, never saved or counted."""
    from api.routes.materialize import _approved_survey
    from src.engines.qc.rules import rules_from_survey

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    try:
        proposed = rules_from_survey(_approved_survey(db, study_id))
    except Exception:  # no approved/readable questionnaire: nothing to propose
        proposed = []
    return {"rules": _cleaning(study)["rules"], "proposed": proposed}


@router.put("/studies/{study_id}/cleaning/rules")
def put_rules(
    study_id: str,
    body: RulesRequest,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    from src.core.models import DataMap
    from src.engines.qc.rules import validate_rules

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    state = _cleaning(study)
    if state["locked"]:
        raise HTTPException(status_code=409, detail="Cleaning is locked for this study")
    _, datamap = _run_artifacts(study, f"Study {study_id}")
    problems = validate_rules(body.rules, DataMap.from_json(datamap.read_text()))
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    _save_cleaning(db, study_id, {**state, "rules": body.rules})
    record_audit(
        db, entity="cleaning.rules", entity_id=study_id, action="set", actor_id=user.id,
        study_id=study_id, before={"rules": state["rules"]}, after={"rules": body.rules},
    )
    db.commit()
    return {"rules": body.rules}


class ImputationRequest(BaseModel):
    variable: str
    method: Literal["mean", "median", "hot_deck"]
    target: Literal["missing", "out_of_range"]
    donor_class: str | None = None
    min: float | None = None
    max: float | None = None
    confirm: bool = False


@router.post("/studies/{study_id}/cleaning/imputations")
def post_imputation(
    study_id: str,
    body: ImputationRequest,
    preview: bool = True,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Preview the cells a policy would change (writes nothing), or store it with confirm=true."""
    from src.core.models import DataMap
    from src.engines.qc.impute import plan_imputation

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    state = _cleaning(study)
    if state["locked"]:
        raise HTTPException(status_code=409, detail="Cleaning is locked for this study")
    if not preview and not body.confirm:
        raise HTTPException(
            status_code=422, detail="Storing an imputation policy needs confirm: true"
        )
    parquet, datamap_path = _run_artifacts(study, f"Study {study_id}")
    raw = parquet.parent / "data_raw.parquet"
    try:
        plan = plan_imputation(
            pl_read(raw if raw.exists() else parquet), body.variable, body.method, body.target,
            body.donor_class, datamap=DataMap.from_json(datamap_path.read_text()),
            survey=_survey_or_none(db, study_id), min=body.min, max=body.max,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if preview:
        return {"preview": True, "n_cells": len(plan), "cells": plan}
    policy = {**body.model_dump(exclude={"confirm"}), "decided_by": user.name,
              "decided_at": datetime.now().isoformat()}
    others = [p for p in state["imputations"] if p["variable"] != body.variable]
    _save_cleaning(db, study_id, {**state, "imputations": [*others, policy]})
    record_audit(
        db, entity="cleaning.imputation", entity_id=body.variable, action="impute",
        actor_id=user.id, study_id=study_id,
        after={"variable": body.variable, "method": body.method, "target": body.target,
               "n_cells": len(plan)},
    )
    db.commit()
    return {"preview": False, "n_cells": len(plan), "policy": policy}
