"""Seed the demo segment surveys into the DB and open a public fielding link each.

Loads scripts/demo_questionnaires.json (6 validated 20-question SurveyDefinitions)
and, per segment, creates the study + an APPROVED questionnaire + an OPEN fielding
link — indistinguishable from rows created through the UI — then prints a table of
copy-pasteable public respondent URLs.

Idempotent: keyed on a deterministic study id (`demo-<segment_slug>`). Re-running
reuses the existing study/questionnaire and, if an open link already exists, prints
it rather than opening a second one. Direct DB access via SessionLocal (operator
script, no HTTP/auth); create_fielding_link still does the survey snapshotting.

Run: PYTHONPATH=. .venv/bin/python scripts/seed_demo_surveys.py
     [--base-url https://web-nu-peach-87.vercel.app]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.db import (  # noqa: E402
    SessionLocal,
    create_fielding_link,
    create_questionnaire,
    create_study,
    get_study,
    init_db,
    list_fielding_links_for_study,
    list_questionnaires_for_study,
    set_questionnaire_approved_qids,
    update_questionnaire_status,
)
from src.core.survey_definition import SurveyDefinition  # noqa: E402

FIXTURE = ROOT / "scripts" / "demo_questionnaires.json"
SEEDED_BY = "seed_demo_surveys@dvv.local"


def _study_id(segment_slug: str) -> str:
    return f"demo-{segment_slug}"


def seed(db, fixtures: list[dict]) -> list[dict]:
    """Idempotently seed each fixture; return one row per segment for printing.

    Each row: {segment, study_id, token, n_questions, created (bool)}.
    """
    rows: list[dict] = []
    for fx in fixtures:
        segment = fx["segment_slug"]
        study_id = _study_id(segment)

        # Build the approved SurveyDefinition from the fixture survey dict.
        survey = SurveyDefinition.from_dict(fx["survey"])
        survey.study_id = study_id  # pin to the deterministic id
        survey.source = "authored"
        survey.status = "approved"
        qids = [q.qid for q in survey.questions]
        survey_json = survey.to_json()

        created = False

        # 1) Study (deterministic id -> reuse on re-run).
        if get_study(db, study_id) is None:
            create_study(db, study_id, client_name=fx["client_name"])
            created = True

        # 2) Approved questionnaire (reuse the first approved one if present).
        questionnaires = list_questionnaires_for_study(db, study_id)
        approved = next((q for q in questionnaires if q.status == "approved"), None)
        if approved is None:
            approved = create_questionnaire(
                db, study_id, source_filename=f"{segment} (seeded)", version=survey.version
            )
            update_questionnaire_status(
                db, approved.id, status="approved", definition_json=survey_json
            )
            set_questionnaire_approved_qids(db, approved.id, qids)

        # 3) Open fielding link (reuse an existing open one -> no duplicate).
        links = list_fielding_links_for_study(db, study_id)
        link = next((link_ for link_ in links if link_.status == "open"), None)
        if link is None:
            link = create_fielding_link(
                db,
                study_id=study_id,
                questionnaire_id=approved.id,
                survey_version=survey.version,
                survey_json=survey_json,
                created_by=SEEDED_BY,
            )

        rows.append(
            {
                "segment": segment,
                "study_id": study_id,
                "token": link.token,
                "n_questions": len(qids),
                "created": created,
            }
        )
    return rows


def _print_table(rows: list[dict], base_url: str) -> None:
    base = base_url.rstrip("/")
    seg_w = max(len("segment"), *(len(r["segment"]) for r in rows))
    sid_w = max(len("study_id"), *(len(r["study_id"]) for r in rows))
    header = f"{'segment':<{seg_w}}  {'study_id':<{sid_w}}  public URL"
    print(header)
    print("-" * len(header))
    for r in rows:
        url = f"{base}/r/{r['token']}"
        print(f"{r['segment']:<{seg_w}}  {r['study_id']:<{sid_w}}  {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://localhost:3000",
        help="Base URL prefixed to each /r/{token} link (default: localhost:3000)",
    )
    args = parser.parse_args()

    init_db()  # ensure tables exist (idempotent)
    fixtures = json.loads(FIXTURE.read_text())

    db = SessionLocal()
    try:
        rows = seed(db, fixtures)
    finally:
        db.close()

    _print_table(rows, args.base_url)
    n_new = sum(1 for r in rows if r["created"])
    print(f"\n{len(rows)} segments seeded ({n_new} new studies this run).")


if __name__ == "__main__":
    main()
