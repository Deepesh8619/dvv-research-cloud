"""field_close: data file, tab plan, a report draft when one exists, then the decks."""

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.db import get_study
from src.platform import jobs
from src.platform.jobs import JobError, handler
from src.platform.workflows import Step, Workflow, register_workflow


def _prepared(db: Session, payload: dict):
    from api.routes.materialize import STUDIES_DIR
    from src.core.models import DataMap

    study_id = payload["inputs"]["study_id"]
    run_dir = Path(STUDIES_DIR) / study_id
    datamap = DataMap.from_json((run_dir / "datamap.json").read_text())
    return get_study(db, study_id), run_dir, datamap


@handler("field_close.materialize")
def _materialize(payload: dict, db: Session) -> dict:
    from api.routes.materialize import build_study_dataset

    try:
        out = build_study_dataset(db, payload["inputs"]["study_id"], publish=False)
    except HTTPException as e:
        raise JobError(str(e.detail)) from None
    return {"n_respondents": out["n_respondents"], "n_variables": out["n_variables"]}


@handler("field_close.tabs")
def _tabs(payload: dict, db: Session) -> dict:
    from api.routes.materialize import publish_tabs

    return {"n_tables": publish_tabs(db, *_prepared(db, payload))}


@handler("field_close.report")
def _report(payload: dict, db: Session) -> dict:
    fn = jobs.HANDLERS.get("report.draft")
    return fn(payload, db) if fn else {"skipped": "no report writer is installed"}


@handler("field_close.deck")
def _deck(payload: dict, db: Session) -> dict:
    from api.routes.materialize import publish_deck

    study, run_dir, datamap = _prepared(db, payload)
    publish_deck(db, study, run_dir, datamap)
    return {"pptx": study.pptx_path, "xlsx": study.excel_path}


register_workflow(Workflow("field_close", (
    Step("materialize", "field_close.materialize"),
    Step("tabs", "field_close.tabs", ("materialize",)),
    Step("report", "field_close.report", ("tabs",)),
    Step("deck", "field_close.deck", ("report",)),
)))
