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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.db import (  # noqa: E402
    Base,
    SessionLocal,
    _ensure_new_columns,
    create_fielding_link,
    create_questionnaire,
    create_study,
    engine,
    get_study,
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


def seed(db, fixtures: list[dict], refresh: bool = False) -> list[dict]:
    """Idempotently seed each fixture; return one row per segment for printing.

    Each row: {segment, study_id, token, n_questions, created (bool)}.

    `refresh=True` (--refresh-definitions): for an already-seeded demo study,
    overwrite the approved questionnaire's `definition_json` AND its open fielding
    link's `survey_json` snapshot IN PLACE with the current fixture. This is how a
    fixture change (e.g. adding `metadata.theme`) reaches links that were pinned
    before the change. Safe ONLY for demo studies (`demo-` id prefix) because
    their links have no real respondents whose in-flight snapshot must be
    preserved — never do this to a production link with live sessions.
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
        elif refresh:
            # Demo-only in-place refresh of the pinned definition (see docstring).
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
        elif refresh:
            # Overwrite the pinned snapshot in place (demo links only).
            link.survey_json = survey_json
            db.commit()
            db.refresh(link)

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


def _prepare_db(assume_yes: bool) -> None:
    """Create tables only — NEVER seed the admin user.

    `init_db()` also calls `seed_admin_user()`, which upserts a default-password
    owner account. Running this operator script against PRODUCTION therefore
    minted (and re-synced) a real admin login — the dangerous bug this guards.
    We create the schema and nothing else. `_ensure_new_columns()` is kept
    (harmless additive migration); admin seeding is dropped entirely.

    Before writing to a non-sqlite DB, print the target host (scheme+host only,
    NEVER credentials) and give a 3-second abort window unless `--yes` is passed.
    """
    url = engine.url  # SQLAlchemy URL object; str(url) would leak the password.
    where = f"{url.drivername}://{url.host or '(local file)'}"
    print(f"seed_demo_surveys: writing to {where}")
    if url.get_backend_name() != "sqlite" and not assume_yes:
        print("  Non-sqlite database. Ctrl-C within 3s to abort (or pass --yes).")
        time.sleep(3)
    Base.metadata.create_all(bind=engine)
    _ensure_new_columns()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://localhost:3000",
        help="Base URL prefixed to each /r/{token} link (default: localhost:3000)",
    )
    parser.add_argument(
        "--refresh-definitions",
        action="store_true",
        help="Overwrite existing demo studies' definition + open-link snapshot "
        "in place with the current fixture (demo studies only; safe because "
        "demo links have no real respondents).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the 3-second abort window when writing to a non-sqlite DB.",
    )
    args = parser.parse_args()

    _prepare_db(args.yes)
    fixtures = json.loads(FIXTURE.read_text())

    db = SessionLocal()
    try:
        rows = seed(db, fixtures, refresh=args.refresh_definitions)
    finally:
        db.close()

    _print_table(rows, args.base_url)
    n_new = sum(1 for r in rows if r["created"])
    print(f"\n{len(rows)} segments seeded ({n_new} new studies this run).")


if __name__ == "__main__":
    main()
