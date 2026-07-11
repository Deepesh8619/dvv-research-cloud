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

import logging
import sys
from pathlib import Path

import polars as pl
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.auth import UserRole, require_role
from api.config import STUDIES_DIR
from api.db import (
    UserDB,
    get_db,
    get_fielding_link,
    get_session_answers_map,
    get_study,
    list_respondent_sessions,
    set_study_artifacts,
    update_study_paths,
    update_study_status,
)
from src.core.survey_definition import SurveyDefinition
from src.engines.fielding.materialize import STATUS_COMPLETE, materialize_fielding
from src.engines.tabs.runner import deck_to_json, run_tab_plan
from src.orchestration.orchestrator import auto_tab_plan
from src.renderers.excel import render_deck as render_excel

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/studies/{study_id}/fielding/links/{link_id}/materialize")
async def materialize_link(
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

    answers = {s.id: get_session_answers_map(db, s.id) for s in completes}
    datamap, df = materialize_fielding(survey, completes, answers)

    # Write to the run-dir convention the read path expects. Reuse the study's
    # own dir under STUDIES_DIR; excel_path.parent must resolve here (that's how
    # _study_dir locates data.parquet/datamap.json).
    run_dir = STUDIES_DIR / study_id
    run_dir.mkdir(parents=True, exist_ok=True)
    datamap_path = run_dir / "datamap.json"
    parquet_path = run_dir / "data.parquet"
    datamap_path.write_text(datamap.to_json())
    # nan_to_null mirrors the orchestrator's pl.from_pandas load step so missing
    # cells are true parquet nulls, exactly like an uploaded study.
    pl.from_pandas(df, nan_to_null=True).write_parquet(str(parquet_path))

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
    update_study_status(db, study_id, "rendered", {"materialized_from_link": link_id})

    logger.info(
        f"[{study_id}] Materialized link {link_id}: {len(df)} respondents, "
        f"{len(datamap)} variables by {user.name}"
    )
    return {
        "n_respondents": len(df),
        "n_variables": len(datamap),
        "destination": str(run_dir),
    }
