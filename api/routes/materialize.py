"""Materialize a fielding link's completed responses into an analyzable study.

The T11 API trigger: load the PINNED survey snapshot + this link's completed
sessions/answers, run the pure `materialize_fielding` transform, and write the
artifacts to WHERE the existing read path already looks — the orchestrator run
dir (`data.parquet`, `datamap.json`, `crosstabs.json`) PLUS the durable DB
columns (`studies.datamap_json`, `studies.crosstabs_json`) that survive a Render
free-tier restart (see api/routes/chat.py `_load_datamap`/`_study_dir` and
api/routes/studies.py `get_crosstabs`). Setting `studies.excel_path` is what the
frontend gates the analysis tabs on (ProjectPage: `available: !!study.excel_path`).

Idempotent: re-materializing after more completes overwrites the run dir with
the fresh full dataset. Zero completes -> 422, not a crash.
"""

import json
import logging
import os

import polars as pl
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import UserRole, get_current_user, require_role
from api.config import STUDIES_DIR
from api.db import (
    UserDB,
    answers_by_session,
    get_db,
    get_fielding_link,
    get_study,
    list_questionnaires_for_study,
    list_respondent_sessions,
    set_study_artifacts,
    update_study_paths,
    update_study_status,
)
from src.adapters.spss import write_sav
from src.core.survey_definition import SurveyDefinition
from src.engines.fielding.materialize import (
    ALL_STATUSES,
    STATUS_COMPLETE,
    materialize_fielding,
)
from src.engines.tabs.runner import deck_to_json, run_tab_plan
from src.orchestration.orchestrator import auto_tab_plan
from src.renderers.excel import render_deck as render_excel

logger = logging.getLogger(__name__)
router = APIRouter()


class SettingsBody(BaseModel):
    mask_pii: bool


def _approved_survey(db: Session, study_id: str) -> SurveyDefinition:
    """The study's approved questionnaire, or a 409 saying why there is none."""
    from api.db import list_questionnaires_for_study

    questionnaires = list_questionnaires_for_study(db, study_id)
    approved = next((q for q in questionnaires if q.status == "approved"), None)
    if approved is None or not approved.definition_json:
        raise HTTPException(
            status_code=409,
            detail="This study has no approved questionnaire, so its data layout is "
                   "not final yet.",
        )
    return SurveyDefinition.from_json(approved.definition_json)


def _publish_tables(db: Session, study, run_dir, datamap) -> None:
    """(Re)build crosstabs + deck from run_dir/data.parquet and point the study at them."""
    study_id = study.id
    parquet_path = run_dir / "data.parquet"
    # Auto crosstabs deck so the Tables tab / durable crosstabs_json activate,
    # same machinery the orchestrator uses. Skipped cleanly if no tabulatable
    # (single-coded) questions exist.
    crosstabs_json = None
    # Fallback when there are no tabulatable (single-coded) questions: keep the
    # marker. excel_path only needs to point INTO run_dir for _study_dir to
    # resolve it; the deck download then 404s, which download_deck already
    # handles gracefully (it 404s on any missing file, never 500s).
    excel_path = str(run_dir / "fielded.marker")
    tab_plan = auto_tab_plan(datamap, title=f"{study.client_name} — Fielded Topline")
    if tab_plan:
        deck_run = run_tab_plan(str(parquet_path), tab_plan, datamap)
        deck_to_json(deck_run, run_dir / "crosstabs.json")
        crosstabs_json = (run_dir / "crosstabs.json").read_text()
        # Render the SAME deck to .xlsx via the existing renderer — the exact
        # call the orchestrator uses for uploaded studies. Ephemeral disk is
        # acceptable for v1: re-materializing (one click) regenerates it.
        xlsx_path = run_dir / f"{study_id}_fielded_deck.xlsx"
        render_excel(deck_run, str(xlsx_path))
        excel_path = str(xlsx_path)
    update_study_paths(db, study_id, excel_path=excel_path, datamap_json=datamap.to_json())
    if crosstabs_json is not None:
        set_study_artifacts(db, study_id, crosstabs_json=crosstabs_json)


def _write_and_publish(db, study, datamap, df, delivery_map, delivery_df):
    """Shared tail: write study-level data + delivery files, re-apply a locked
    cleaning version, publish tables. Returns (run_dir, df, delivery_df, removed, exports)."""
    study_id = study.id
    # Write to the run-dir convention the read path expects. Reuse the study's
    # own dir under STUDIES_DIR; excel_path.parent must resolve here (that's how
    # _study_dir locates data.parquet/datamap.json).
    run_dir = STUDIES_DIR / study_id
    run_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = run_dir / "data.parquet"
    (run_dir / "datamap.json").write_text(datamap.to_json())
    # nan_to_null mirrors the orchestrator's pl.from_pandas load step so missing
    # cells are true parquet nulls, exactly like an uploaded study.
    # A fresh data.parquet makes any data_raw.parquet stale (apply_cleaning would
    # rebuild from the OLD respondents), so drop it before writing.
    (run_dir / "data_raw.parquet").unlink(missing_ok=True)
    pl.from_pandas(df, nan_to_null=True).write_parquet(str(parquet_path))
    cleaning = (study.study_metadata or {}).get("cleaning") or {}
    removed: dict = {}
    if cleaning.get("locked") and cleaning.get("versions"):
        from src.engines.qc.cleaning import apply_cleaning

        latest = cleaning["versions"][-1]
        removed = dict(latest.get("removed") or {})
        apply_cleaning(run_dir, removed, latest.get("imputations") or [])
        df = pl.read_parquet(parquet_path)  # n_respondents/tables see the cleaned set

    # The DELIVERY files, separate from the analysis artifacts above on purpose.
    # A topline computed over screened-out respondents is simply wrong, so
    # data.parquet stays completes-only — but a delivery carries every
    # disposition, because the terminates are the evidence the screen-outs
    # worked and the denominator of the incidence rate the client is buying.
    if removed:
        from src.engines.qc.cleaning import annotate_delivery

        delivery_df, delivery_map = annotate_delivery(
            pl.from_pandas(delivery_df, nan_to_null=True), delivery_map, removed
        )
        delivery_df = delivery_df.to_pandas()
    if (study.study_metadata or {}).get("mask_pii"):
        delivery_df = _mask_delivery(delivery_df, delivery_map)
    exports: dict[str, str] = {}
    sav_path = run_dir / f"{study_id}_responses.sav"
    try:
        write_sav(delivery_df, delivery_map, str(sav_path))
        exports["sav"] = str(sav_path)
    except Exception as exc:  # a delivery format must never fail the pipeline
        logger.warning(f"[{study_id}] SPSS export failed (non-fatal): {exc}")
    responses_xlsx = run_dir / f"{study_id}_responses.xlsx"
    try:
        _write_labelled_xlsx(delivery_df, delivery_map, responses_xlsx)
        exports["xlsx"] = str(responses_xlsx)
    except Exception as exc:
        logger.warning(f"[{study_id}] respondent-level xlsx failed (non-fatal): {exc}")

    _publish_tables(db, study, run_dir, datamap)
    # Erasure sets this; a successful write is what clears it.
    study.study_metadata = {**(study.study_metadata or {}), "needs_rematerialize": False}
    db.commit()
    return run_dir, df, delivery_df, removed, exports


def _mask_delivery(delivery_df, delivery_map):
    """A masked COPY for the delivery files; data.parquet stays raw."""
    from src.core.models import VarType
    from src.engines.qc.pii import mask_text, pii_columns

    out = delivery_df.copy()
    pii = set(pii_columns(delivery_map))
    for var in delivery_map.variables:
        if var.name not in out.columns:
            continue
        if var.name in pii:
            out[var.name] = out[var.name].astype(object).where(out[var.name].isna(), "***")
        elif var.type is VarType.TEXT:
            out[var.name] = out[var.name].map(mask_text)
    return out


@router.patch("/studies/{study_id}/settings")
def update_settings(
    study_id: str,
    body: SettingsBody,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER)),
):
    """Study delivery settings. Only `mask_pii` today. (Owner)"""
    from api.db import record_audit

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    old = bool((study.study_metadata or {}).get("mask_pii"))
    study.study_metadata = {**(study.study_metadata or {}), "mask_pii": body.mask_pii}
    record_audit(
        db, entity="compliance.settings", entity_id=study_id, action="set",
        actor_id=user.id, study_id=study_id, summary=f"mask_pii {old} -> {body.mask_pii}",
    )
    db.commit()
    return {"mask_pii": body.mask_pii}


def _replacement(db, link_id, removed, answers) -> dict:
    replacement = {"total": 0, "by_cell": {}}
    if removed:
        from api.db import list_quota_cells
        from src.core.survey_definition import Expression
        from src.engines.fielding.decisions import QuotaCell
        from src.engines.qc.cleaning import replacement_needed

        cells = [
            QuotaCell(id=c.id, condition=Expression.from_dict(json.loads(c.condition_json)),
                      limit=c.cell_limit, count=c.count)
            for c in list_quota_cells(db, link_id)
        ]
        replacement = replacement_needed(
            {k: answers.get(k, {}) for k in removed if k in answers}, cells
        )
    return replacement


_REPLACEMENT_NOTE = (
    "Quota counts are NOT decremented for removed respondents. "
    "Reopening a cell to field replacements is a person's call, "
    "made through PATCH /quotas/{id}."
)


@router.post("/studies/{study_id}/fielding/links/{link_id}/materialize")
def materialize_link(
    study_id: str,
    link_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Materialize a link's completed responses into the study's analysis artifacts.

    (Owner/Analyst) Returns `{n_respondents, n_variables, destination}`.
    """
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    link = get_fielding_link(db, link_id)
    if not link or link.study_id != study_id:
        raise HTTPException(status_code=404, detail="Fielding link not found")

    survey = SurveyDefinition.from_json(link.survey_json)
    sessions = list_respondent_sessions(db, link_id)
    completes = [s for s in sessions if s.status_code == STATUS_COMPLETE]
    if not completes:
        raise HTTPException(
            status_code=422,
            detail="No completed sessions to materialize (need at least one status_code=10)",
        )

    answers = answers_by_session(db, link_id)  # one query, not one per respondent
    datamap, df = materialize_fielding(survey, completes, answers)

    delivery_map, delivery_df = materialize_fielding(
        survey, sessions, answers, statuses=ALL_STATUSES
    )
    run_dir, df, delivery_df, removed, exports = _write_and_publish(
        db, study, datamap, df, delivery_map, delivery_df
    )
    update_study_status(db, study_id, "rendered", {"materialized_from_link": link_id})

    replacement = _replacement(db, link_id, removed, answers)

    logger.info(
        f"[{study_id}] Materialized link {link_id}: {len(df)} respondents, "
        f"{len(datamap)} variables by {user.name}"
    )
    return {
        "n_respondents": len(df),
        "n_variables": len(datamap),
        "destination": str(run_dir),
        "delivery": {
            "n_rows": len(delivery_df),
            "formats": sorted(exports),
        },
        "replacement_needed": replacement,
        "replacement_note": _REPLACEMENT_NOTE,
    }


def _merge_datamaps(maps: list, versions: list[tuple[int, object]]):
    """Union of datamaps by variable name; 409 on conflicts or unmergeable versions."""
    from src.core.models import DataMap

    distinct = sorted({v for v, _ in versions})
    if len(distinct) > 1 and len({tuple(m.names()) for m in maps}) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"Links are pinned to survey versions {distinct} with different "
                   "question sets; they cannot be combined into one dataset.",
        )
    merged = DataMap()
    for m in maps:
        for var in m.variables:
            if var.name not in merged:
                merged.add(var)
                continue
            have = merged[var.name]
            if have.type != var.type or have.values != var.values:
                raise HTTPException(
                    status_code=409,
                    detail=f"Variable {var.name} has conflicting types or value labels "
                           f"across links (survey versions {distinct}).",
                )
    return merged


@router.post("/studies/{study_id}/fielding/materialize")
def materialize_study(
    study_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Materialize ALL non-test links into ONE study dataset with a `vSource` column.

    (Owner/Analyst) Same outputs as the per-link route, combined across vendors.
    """
    import pandas as pd

    from api.db import list_fielding_links_for_study
    from src.core.models import Variable, VarType

    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    links = sorted(
        (lk for lk in list_fielding_links_for_study(db, study_id) if not lk.is_test),
        key=lambda lk: lk.created_at or "",
    )
    parts = []  # (link, datamap, df, delivery_map, delivery_df, answers)
    for n, link in enumerate(links, 1):
        survey = SurveyDefinition.from_json(link.survey_json)
        sessions = list_respondent_sessions(db, link.id)
        completes = [s for s in sessions if s.status_code == STATUS_COMPLETE]
        if not completes:
            continue
        answers = answers_by_session(db, link.id)
        dmap, df = materialize_fielding(survey, completes, answers)
        xmap, xdf = materialize_fielding(survey, sessions, answers, statuses=ALL_STATUSES)
        parts.append((link, f"Link {n}", dmap, df, xmap, xdf, answers))
    if not parts:
        raise HTTPException(
            status_code=422,
            detail="No completed sessions to materialize (need at least one status_code=10)",
        )

    versions = [(p[0].survey_version, p[0]) for p in parts]
    datamap = _merge_datamaps([p[2] for p in parts], versions)
    delivery_map = _merge_datamaps([p[4] for p in parts], versions)

    labels = {p[0].id: (p[0].vendor_name or p[1]) for p in parts}
    codes = {lab: i for i, lab in enumerate(sorted(set(labels.values())), 1)}
    for dm in (datamap, delivery_map):
        dm.add(Variable(
            name="vSource", label="Sample source (vendor)", type=VarType.SINGLE,
            values={c: lab for lab, c in codes.items()}, provenance="fielding",
        ))

    def _combine(idx: int):
        frames = []
        for p in parts:
            f = p[idx].copy()
            f["vSource"] = codes[labels[p[0].id]]
            frames.append(f)
        out = pd.concat(frames, ignore_index=True)
        out["respondent_id"] = pd.Series(range(1, len(out) + 1), dtype="int64")
        return out

    df, delivery_df = _combine(3), _combine(5)
    run_dir, df, delivery_df, removed, exports = _write_and_publish(
        db, study, datamap, df, delivery_map, delivery_df
    )
    update_study_status(
        db, study_id, "rendered", {"materialized_from_links": [p[0].id for p in parts]}
    )

    by_cell: dict = {}
    total = 0
    for p in parts:
        rep = _replacement(db, p[0].id, removed, p[6])
        total += rep["total"]
        for k, v in rep["by_cell"].items():
            by_cell[k] = by_cell.get(k, 0) + v
    logger.info(
        f"[{study_id}] Materialized {len(parts)} links: {len(df)} respondents by {user.name}"
    )
    return {
        "n_respondents": len(df),
        "n_variables": len(datamap),
        "n_links": len(parts),
        "sources": {str(c): lab for lab, c in codes.items()},
        "destination": str(run_dir),
        "delivery": {"n_rows": len(delivery_df), "formats": sorted(exports)},
        "replacement_needed": {"total": total, "by_cell": by_cell},
        "replacement_note": _REPLACEMENT_NOTE,
    }


def _write_labelled_xlsx(df, datamap, path) -> None:
    """Respondent-level data with its codebook, in one workbook.

    Two sheets, because a client opening the file needs both halves and a
    spreadsheet of bare integers is not a deliverable: `Data` is the wide table
    exactly as the data map describes it, and `Datamap` is every variable with
    its label, type and value labels. It mirrors the shape clients send US,
    which the tabular adapter already knows how to read back.
    """
    import pandas as pd

    rows = []
    for variable in datamap.variables:
        rows.append({
            "Variable": variable.name,
            "Label": variable.label or "",
            "Type": str(getattr(variable.type, "value", variable.type)),
            "Values": "; ".join(
                f"{code} = {label}" for code, label in sorted((variable.values or {}).items())
            ),
        })
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Data", index=False)
        pd.DataFrame(rows).to_excel(writer, sheet_name="Datamap", index=False)


@router.get("/studies/{study_id}/test-data")
def test_data(
    study_id: str,
    n: int = 200,
    seed: int = 20260917,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.FOUNDER, UserRole.ANALYST)),
):
    """A dataset for an approved questionnaire, before any respondent exists.

    Everything downstream of the instrument — the tab plan, the banner, the QC
    rules, the deck — can be built and tested against this on the day the
    questionnaire is signed off, instead of waiting for field, which is the most
    expensive place to discover a column is missing.

    The respondents come from the dry-run walk, so they obey the survey's own
    bases, masks, screen-outs and quotas: the dummy data has the same holes in
    the same places as the real thing. Every row is synthetic and marked as
    such — no respondent said any of it. (Owner/Founder/Analyst)
    """
    from api.heavy import run_heavy_detached
    from src.engines.fielding.testdata import synthesise

    survey = _approved_survey(db, study_id)
    datamap, frame = run_heavy_detached(db, synthesise, survey, None, n, seed)
    return {
        "study_id": study_id,
        "synthetic": True,
        "note": "Generated from the questionnaire's own conditions. No respondent "
                "answered any of this; it is for building and testing what comes "
                "after the instrument.",
        "seed": seed,
        "n_rows": len(frame),
        "n_variables": len(datamap.variables),
        "datamap": [
            {"name": v.name, "label": v.label,
             "type": v.type.value if hasattr(v.type, "value") else str(v.type)}
            for v in datamap.variables
        ],
        "rows": frame.head(min(n, 500)).where(frame.notna(), None).to_dict("records"),
    }


@router.get("/studies/{study_id}/fielding/links/{link_id}/field-report")
def field_report(
    study_id: str,
    link_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """What actually happened in the field. (Owner/Analyst)

    The numbers a researcher is asked for the morning after a launch: starts,
    dispositions, incidence, WHICH screen-out removed people, which quota cell
    closed, how long the interview really took, and where respondents gave up.
    All of it already lived in the sessions and had simply never been counted.
    """
    import json as _json

    from src.core.survey_definition import Expression
    from src.engines.fielding.decisions import QuotaCell
    from src.engines.fielding.field_report import build_field_report

    link = get_fielding_link(db, link_id)
    if not link or link.study_id != study_id:
        raise HTTPException(status_code=404, detail="Fielding link not found")

    from api.db import answers_by_session, list_quota_cells
    from api.heavy import plain_sessions, run_heavy_detached

    survey = SurveyDefinition.from_json(link.survey_json)
    sessions = list_respondent_sessions(db, link_id)
    answers = answers_by_session(db, link_id)
    cells = [
        QuotaCell(
            id=cell.id,
            condition=Expression.from_dict(_json.loads(cell.condition_json)),
            limit=cell.cell_limit,
            count=cell.count,
        )
        for cell in list_quota_cells(db, link_id)
    ]
    report = run_heavy_detached(
        db, build_field_report, survey, plain_sessions(sessions), answers, cells
    )
    return {**report.to_dict(), "link_id": link_id, "run_by": user.name}


@router.get("/studies/{study_id}/fielding/links/{link_id}/soft-launch-check")
def soft_launch_check(
    study_id: str,
    link_id: str,
    db: Session = Depends(get_db),
    user: UserDB = Depends(require_role(UserRole.OWNER, UserRole.ANALYST)),
):
    """Everything worth knowing before releasing the rest of the sample. (Owner/Analyst)

    A soft launch is only worth the pause if something is actually inspected
    during it. This runs, on the completes captured SO FAR, the same QC
    cross-check the platform runs on a delivered dataset — punches that aren't
    in the questionnaire, screen-outs that let people carry on answering, masks
    that didn't narrow, pipes with an empty source — plus the field report
    (incidence, which screen-out removed people, where they dropped off).

    Read-only: nothing is written to the study, so running it never disturbs
    analysis artifacts from an earlier materialize. Terminates are INCLUDED in
    the checked rows on purpose — they are the only evidence the screen-outs
    fired at all.
    """
    import json as _json

    from api.db import answers_by_session, list_quota_cells
    from api.heavy import plain_sessions, run_heavy_detached
    from src.core.survey_definition import Expression
    from src.engines.fielding.decisions import QuotaCell
    from src.engines.fielding.launch_check import launch_check

    link = get_fielding_link(db, link_id)
    if not link or link.study_id != study_id:
        raise HTTPException(status_code=404, detail="Fielding link not found")

    survey = SurveyDefinition.from_json(link.survey_json)
    sessions = list_respondent_sessions(db, link_id)
    n_completes = sum(1 for s in sessions if s.status_code == STATUS_COMPLETE)
    cells = [
        QuotaCell(
            id=cell.id,
            condition=Expression.from_dict(_json.loads(cell.condition_json)),
            limit=cell.cell_limit,
            count=cell.count,
        )
        for cell in list_quota_cells(db, link_id)
    ]
    answers = answers_by_session(db, link_id)
    result = run_heavy_detached(
        db, launch_check, survey, plain_sessions(sessions), answers, cells
    )
    return {
        "link_id": link_id,
        "soft_launch_limit": link.soft_launch_limit,
        "held": link.soft_launch_limit is not None and n_completes >= link.soft_launch_limit,
        "n_completes": n_completes,
        **result,
        "run_by": user.name,
    }


def data_access(db: Session, study_id: str, user: UserDB):
    """Whether this user may see respondent answers for the study, and why.

    One rule for every door the answers leave by — the delivery files and the
    live view alike. Owners and founders from approval; everyone else once a
    soft launch reaches its hold or a quota cell fills (see fielding/access.py).
    """
    from api.db import count_completes, list_fielding_links_for_study, list_quota_cells
    from src.engines.fielding.access import LinkState, may_download_data

    approved = any(
        q.status == "approved" for q in list_questionnaires_for_study(db, study_id)
    )
    links = list_fielding_links_for_study(db, study_id)
    states = [
        LinkState(
            soft_launch_limit=link.soft_launch_limit, completes=count_completes(db, link.id)
        )
        for link in links
    ]
    quota_filled = any(
        cell.count >= cell.cell_limit for link in links for cell in list_quota_cells(db, link.id)
    )
    role = getattr(user.role, "value", user.role)
    return may_download_data(str(role), approved, states, quota_filled)


_STATUS_LABELS = {10: "complete", 20: "screened out", 30: "security", 40: "over quota"}


def _display(question, value) -> str:
    """One answer as a person reads it: labels, not punch codes."""
    from src.core.survey_definition import QuestionType

    labels = {o.code: o.label for o in (question.options if question else [])}
    is_grid = question is not None and question.type in (
        QuestionType.GRID_SINGLE, QuestionType.GRID_MULTI)
    if is_grid and isinstance(value, dict):
        rows = {str(r.code): r.label for r in question.rows or []}
        cols = {c.code: c.label for c in question.cols or []}

        def picked(c):
            picks = c if isinstance(c, list) else [c]
            return ", ".join(str(cols.get(_as_int(p), p)) for p in picks)

        return "; ".join(f"{rows.get(str(r), r)}: {picked(c)}" for r, c in value.items())
    if isinstance(value, list):
        return ", ".join(str(labels.get(_as_int(v), v)) for v in value)
    return str(labels.get(_as_int(value), value))


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


@router.get("/studies/{study_id}/fielding/links/{link_id}/responses")
def live_responses(
    study_id: str,
    link_id: str,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    """Respondent answers as they arrive, newest first. Gated like a download.

    What a researcher watches in the first hour of field: is anyone getting
    through, and are the answers sane. Labels, not codes, because this is read
    by a person; the delivery files carry the codes.
    """
    decision = data_access(db, study_id, user)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    link = get_fielding_link(db, link_id)
    if not link or link.study_id != study_id:
        raise HTTPException(status_code=404, detail="Fielding link not found")

    survey = SurveyDefinition.from_json(link.survey_json)
    by_qid = {q.qid: q for q in survey.questions}
    order = [
        qid for qid in survey.question_order()
        if by_qid.get(qid) and by_qid[qid].type.value != "info"
    ]
    # The system variables, FIRST, because they are the context the answers are
    # read against. The first hour of field is spent asking who these people
    # are, not what they said: how fast did they go, what were they on, where
    # is the connection, has this connection been here before. They were only
    # in the delivery file, which is written after fielding — the one moment
    # they cannot help.
    #
    # Same summariser the delivery file uses, so the two cannot disagree.
    order = [*_SYSTEM_COLUMNS, *order]
    sessions = sorted(
        list_respondent_sessions(db, link_id),
        key=lambda s: s.last_seen_at or s.started_at or "",
        reverse=True,
    )
    answers = answers_by_session(db, link_id)
    limit = max(1, min(int(limit), 1000))
    study = get_study(db, study_id)
    mask = bool((study.study_metadata or {}).get("mask_pii")) if study else False
    if mask:
        from src.engines.qc.pii import is_pii_label, mask_text
    rows = []
    for session in sessions[:limit]:
        given = answers.get(session.id, {})
        shown = {
            qid: _display(by_qid.get(qid), given[qid]) for qid in order if qid in given
        }
        if mask:
            shown = {
                qid: "***" if is_pii_label(qid, by_qid[qid].text if qid in by_qid else "")
                else mask_text(val)
                for qid, val in shown.items()
            }
        shown.update(_system_values(session))
        rows.append({
            "id": session.id[:8],
            "status": _STATUS_LABELS.get(session.status_code, "in progress"),
            "started_at": session.started_at,
            "last_seen_at": session.last_seen_at,
            "answers": shown,
        })
    return {
        "columns": order,
        "rows": rows,
        "total": len(sessions),
        # Whether the location columns can ever be filled. Without the offline
        # database they are blank for everybody, and a blank that means "not
        # configured" looks exactly like a blank that means "we could not tell"
        # — so it is said outright rather than left to be inferred.
        "location_available": bool(os.environ.get("DVV_GEOIP_DB")),
    }


#: System variables shown beside the answers, in the order a person reads them.
_SYSTEM_COLUMNS = [
    "vDurationSec", "vDevice", "vCountry", "vRegion", "vCity",
    "vPostcode", "vConnection",
]


def _system_values(session) -> dict[str, str]:
    """What the interview itself says about this respondent."""
    from src.engines.fielding.materialize import _parse_iso
    from src.engines.fielding.paradata import summarise as summarise_paradata

    para = summarise_paradata(getattr(session, "entry_params", None) or {})
    started = _parse_iso(getattr(session, "started_at", None))
    finished = _parse_iso(getattr(session, "completed_at", None))
    duration = (
        str(int((finished - started).total_seconds()))
        if started and finished else ""
    )
    return {
        "vDurationSec": duration,
        "vDevice": para.get("device_label") or "",
        "vCountry": para.get("country") or "",
        "vRegion": para.get("region") or "",
        "vCity": para.get("city") or "",
        "vPostcode": para.get("postal") or "",
        # Shortened: it is here so two rows can be COMPARED, and a full hash
        # is forty characters of noise in a table somebody scans.
        "vConnection": (para.get("ip_hash") or "")[:8],
    }
