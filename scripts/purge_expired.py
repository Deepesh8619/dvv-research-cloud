"""Purge data of studies whose retain_until has passed. DRY RUN unless --apply.

IRREVERSIBLE. There is deliberately NO web endpoint for this. Production does not
track main: someone must run it on the server via the deploy tooling, e.g.
    cd /opt/dvv/deploy && ./dvv exec python scripts/purge_expired.py          # dry run FIRST
    cd /opt/dvv/deploy && ./dvv exec python scripts/purge_expired.py --apply
Keeps the study row and every audit row; deletes respondent data, chat, the archived
source file, run/upload files; revokes share links; status becomes "purged".
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.db import (  # noqa: E402
    ChatMessageDB,
    FieldingLinkDB,
    RespondentAnswerDB,
    RespondentSessionDB,
    SessionLocal,
    StudyDB,
    purge_study_data,
)


def expired_studies(db):
    today = datetime.now(UTC).date().isoformat()
    return [s for s in db.query(StudyDB).all()
            if s.status != "purged" and (s.study_metadata or {}).get("retain_until")
            and str(s.study_metadata["retain_until"])[:10] <= today]


def _would_go(db, s):
    lids = [r[0] for r in db.query(FieldingLinkDB.id).filter(FieldingLinkDB.study_id == s.id)]
    sids = [r[0] for r in db.query(RespondentSessionDB.id).filter(
        RespondentSessionDB.fielding_link_id.in_(lids))] if lids else []
    return {
        "sessions": len(sids),
        "answers": db.query(RespondentAnswerDB).filter(
            RespondentAnswerDB.session_id.in_(sids)).count() if sids else 0,
        "chat_messages": db.query(ChatMessageDB).filter(ChatMessageDB.study_id == s.id).count(),
    }


def run(db, apply: bool = False) -> list[tuple[str, dict]]:
    out = []
    for s in expired_studies(db):
        counts = purge_study_data(db, s.id) if apply else _would_go(db, s)
        print(f"{'PURGED' if apply else 'would purge'} {s.id} (retain_until "
              f"{s.study_metadata['retain_until']}): {counts}")
        out.append((s.id, counts))
    print(f"{len(out)} study(ies) {'purged' if apply else 'eligible (dry run; pass --apply)'}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    db = SessionLocal()
    try:
        run(db, ap.parse_args().apply)
    finally:
        db.close()
