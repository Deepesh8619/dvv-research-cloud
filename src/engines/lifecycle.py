"""Where a study stands, read only from facts already stored. Computes nothing."""

import json
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.db import (
    FieldingLinkDB,
    QuestionnaireDB,
    QuotaCellDB,
    RespondentSessionDB,
    StudyDB,
)

# A tab is a ProjectPage tab name, or "/path" for a route under /projects/{id}.
_LABELS = ["Brief", "Questionnaire", "Proof", "Field", "Verify", "Data", "Tables", "Insight"]


def _questionnaire(db: Session, study_id: str) -> tuple[str, str]:
    q = (db.query(QuestionnaireDB).filter(QuestionnaireDB.study_id == study_id)
         .order_by(QuestionnaireDB.created_at.desc()).first())
    if q is None:
        return "todo", "No questionnaire yet"
    if q.status == "approved":
        return "done", "Approved"
    flagged = 0
    try:
        qs = json.loads(q.definition_json or "{}").get("questions", [])
        approved = set(q.approved_qids or [])
        flagged = sum(1 for x in qs if x.get("flags") and x.get("qid") not in approved)
    except (ValueError, AttributeError):
        pass
    if q.status in ("draft", "in_review") and flagged:
        return "attention", f"{flagged} question{'s' if flagged != 1 else ''} flagged for review"
    return "todo", "Not approved yet"


def _field(db: Session, study_id: str) -> tuple[str, str]:
    links = (db.query(FieldingLinkDB).filter(FieldingLinkDB.study_id == study_id,
                                             FieldingLinkDB.is_test.isnot(True)).all())
    if not links:
        return "todo", "No fielding link yet"
    ids = [lk.id for lk in links]
    done = (db.query(func.count(RespondentSessionDB.id))
            .filter(RespondentSessionDB.fielding_link_id.in_(ids),
                    RespondentSessionDB.status_code == 10).scalar() or 0)
    target = sum(lk.target_completes or 0 for lk in links) or (
        db.query(func.sum(QuotaCellDB.cell_limit))
        .filter(QuotaCellDB.fielding_link_id.in_(ids)).scalar() or 0)
    detail = f"{done}/{target} completes" if target else f"{done} completes"
    return ("done" if target and done >= target else "todo"), detail


def _proof(run: dict | None) -> tuple[str, str, str]:
    """The last dry run of the approved questionnaire (recorded by the dry-run route)."""
    if not run:
        return "todo", "Run a dry run to prove the logic", "/fielding"
    problems = (run.get("impossible") or 0) + (run.get("never_fired") or 0)
    unseen = run.get("never_shown") or 0
    if problems or unseen:
        parts = []
        if problems:
            parts.append(f"{problems} rule{'s' if problems != 1 else ''} never fired or impossible")
        if unseen:
            parts.append(f"{unseen} question{'s' if unseen != 1 else ''} nobody reaches")
        return "attention", "Dry run: " + ", ".join(parts), "/fielding"
    return "done", f"Dry run of {run.get('n')} found no logic problems", "/fielding"


def lifecycle(db: Session, study: StudyDB) -> dict:
    meta = study.study_metadata or {}
    cleaning = meta.get("cleaning") or {}
    processed = bool(study.excel_path or study.datamap_json)
    run_dir = Path(study.excel_path).parent if study.excel_path else None
    has_tables = bool(study.crosstabs_json or (run_dir and (run_dir / "crosstabs.json").exists()))

    q_state, q_detail = _questionnaire(db, study.id)
    f_state, f_detail = _field(db, study.id)
    brief = bool(meta.get("description") or meta.get("template") or meta.get("kind"))
    if cleaning.get("locked"):
        v = ("done", "Cleaning locked")
    elif processed and meta.get("qc_passed") is False:
        v = ("attention", "Quality check found issues to review")
    else:
        v = ("todo", "Review respondents in Cleaning" if processed else "Waiting for data")

    rows = [
        ("done" if brief else "todo",
         "Described" if brief else "Add a description or template", "Overview"),
        (q_state, q_detail, "/questionnaire"),
        _proof(meta.get("last_dry_run")),
        (f_state, f_detail, "/fielding"),
        (*v, "Cleaning"),
        ("done" if processed else "todo",
         "Data processed" if processed else "No data file yet", "Overview"),
        ("done" if has_tables else "todo",
         "Tables ready" if has_tables else "No tables yet", "Reports"),
        ("done" if study.pptx_path else "todo",
         "Deck ready" if study.pptx_path else "No report or deck yet", "Reports"),
    ]
    stages = [{"key": lb.lower(), "label": lb, "state": s, "detail": d, "tab": t}
              for lb, (s, d, t) in zip(_LABELS, rows, strict=True)]
    needs = [{"kind": s["key"], "title": f"{s['label']} needs you",
              "detail": s["detail"], "tab": s["tab"]}
             for s in stages if s["state"] == "attention"]
    return {"stages": stages, "needs_you": needs}
