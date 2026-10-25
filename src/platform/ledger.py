"""Tamper-evident study ledger: every audit entry is hashed onto the one before it."""

import hashlib
import json
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.db import AuditLogDB, LedgerHeadDB, record_audit

GLOBAL = "_global"


def _hash(prev_hash: str, e: AuditLogDB) -> str:
    canon = json.dumps(
        {"entity": e.entity, "entity_id": e.entity_id, "action": e.action,
         "actor_id": e.actor_id, "study_id": e.study_id, "summary": e.summary,
         "before": e.before_json, "after": e.after_json, "created_at": e.at},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev_hash + canon).encode()).hexdigest()


def _head(db: Session, key: str) -> LedgerHeadDB:
    """The study's head row, locked until the caller commits (SQLite ignores the lock)."""
    q = db.query(LedgerHeadDB).filter(LedgerHeadDB.study_id == key).with_for_update()
    head = q.one_or_none()
    if head is None:
        try:
            with db.begin_nested():
                db.add(LedgerHeadDB(study_id=key, seq=0, hash=""))
        except IntegrityError:  # another writer created it first
            pass
        head = q.one()
    return head


def chain(db: Session, entry: AuditLogDB) -> None:
    """Give `entry` the next seq and hash for its study and move the head."""
    head = _head(db, entry.study_id or GLOBAL)
    entry.seq = head.seq + 1
    entry.prev_hash = head.hash
    entry.hash = _hash(head.hash, entry)
    head.seq, head.hash = entry.seq, entry.hash


def verify(db: Session, study_id: str | None) -> dict:
    """Recompute the chain. `first_bad_seq` is the first entry that does not match."""
    sid = study_id or None
    rows = db.query(AuditLogDB).filter(
        AuditLogDB.study_id == sid if sid else AuditLogDB.study_id.is_(None))
    unchained = rows.filter(AuditLogDB.seq.is_(None)).count()
    prev, n, bad = "", 0, None
    for e in rows.filter(AuditLogDB.seq.isnot(None)).order_by(AuditLogDB.seq):
        n += 1
        if e.seq != n or e.prev_hash != prev or e.hash != _hash(prev, e):
            bad = e.seq if e.seq else n
            break
        prev = e.hash
    head = db.get(LedgerHeadDB, sid or GLOBAL)
    # A deleted tail leaves a valid chain that ends before the head.
    if bad is None and head is not None and (head.seq, head.hash) != (n, prev):
        bad = n + 1
    out = {"ok": bad is None, "n": n, "head": prev, "first_bad_seq": bad}
    if unchained:
        out["unchained"] = unchained
    return out


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def issue(db: Session, study_id: str, path, actor_id: str = "system") -> None:
    """Record that this file was handed out. Never raises, like record_audit.
    The caller commits."""
    try:
        p = Path(path)
        record_audit(db, entity="deliverable", entity_id=p.name, action="issued",
                     actor_id=actor_id, study_id=study_id, summary=f"Issued {p.name}",
                     after={"file": p.name, "sha256": file_sha256(p)})
    except Exception:  # noqa: BLE001
        pass


def was_issued(db: Session, study_id: str, sha256: str) -> dict | None:
    e = (db.query(AuditLogDB)
         .filter(AuditLogDB.study_id == study_id, AuditLogDB.entity == "deliverable",
                 AuditLogDB.action == "issued", AuditLogDB.after_json.contains(sha256))
         .order_by(AuditLogDB.at).first())
    if e is None:
        return None
    return {"at": e.at, "name": json.loads(e.after_json)["file"]}
