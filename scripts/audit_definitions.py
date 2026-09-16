#!/usr/bin/env python3
"""Report which stored questionnaires still carry a pre-fix extraction defect.

Run it after an upgrade, against whatever database the app is pointed at:

    python3 scripts/audit_definitions.py            # local
    docker compose exec -T app python3 scripts/audit_definitions.py   # a client stack

Exit code 1 when something is affected, so it can gate a deploy step.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.db import QuestionnaireDB, SessionLocal  # noqa: E402
from src.engines.questionnaire.definition_audit import audit_all, format_report  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        rows = db.query(QuestionnaireDB).all()
        audits = audit_all(rows)
    finally:
        db.close()
    print(format_report(audits))
    return 1 if any(not a.clean for a in audits) else 0


if __name__ == "__main__":
    raise SystemExit(main())
