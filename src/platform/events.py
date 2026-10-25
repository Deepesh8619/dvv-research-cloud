"""Per-study event log: what the live stream and the control room read."""

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from api.db import EventDB


def emit(db: Session, study_id: str, kind: str, data: dict | None = None) -> int:
    ev = EventDB(
        study_id=study_id,
        kind=kind,
        data=json.dumps(data or {}),
        created_at=datetime.now(UTC).isoformat(),
    )
    db.add(ev)
    db.commit()
    return ev.seq


def since(db: Session, study_id: str, after_seq: int, limit: int = 200) -> list[dict]:
    rows = (
        db.query(EventDB)
        .filter(EventDB.study_id == study_id, EventDB.seq > after_seq)
        .order_by(EventDB.seq)
        .limit(limit)
        .all()
    )
    return [
        {"seq": r.seq, "kind": r.kind, "data": json.loads(r.data or "{}"),
         "created_at": r.created_at}
        for r in rows
    ]
