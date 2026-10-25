"""Study graph: what each thing in a study was computed from, and what depends on it.

An edge points from the thing that depends to the thing it comes from:
`cell -computed_from-> variable`, `sentence -cites-> cell`.
"""

import json
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.db import GraphEdgeDB, GraphNodeDB


def _now() -> str:
    return datetime.now(UTC).isoformat()


def node_id(study_id: str, kind: str, ref: str) -> str:
    return f"{study_id}:{kind}:{ref}"


def upsert_node(
    db: Session, study_id: str, kind: str, ref: str, label: str = "", props: dict | None = None
) -> str:
    """Insert, or update label/props and bump version if they changed. No commit."""
    nid = node_id(study_id, kind, ref)
    p = json.dumps(props or {}, sort_keys=True)
    row = db.get(GraphNodeDB, nid)
    if row is None:
        db.add(GraphNodeDB(id=nid, study_id=study_id, kind=kind, ref=ref, label=label, props=p,
                           version=1, created_at=_now(), updated_at=_now()))
    elif (row.label, row.props) != (label, p):
        row.label, row.props, row.version, row.updated_at = label, p, (row.version or 1) + 1, _now()
    db.flush()
    return nid


def link(
    db: Session, study_id: str, src: str, dst: str, kind: str, props: dict | None = None
) -> str:
    eid = f"{src}|{kind}|{dst}"
    if db.get(GraphEdgeDB, eid) is None:
        db.add(GraphEdgeDB(id=eid, study_id=study_id, src=src, dst=dst, kind=kind,
                           props=json.dumps(props or {}, sort_keys=True), created_at=_now()))
        db.flush()
    return eid


def unlink_kind(db: Session, study_id: str, src: str, kind: str) -> None:
    db.query(GraphEdgeDB).filter_by(study_id=study_id, src=src, kind=kind).delete()


# {frm}/{to} are fixed column names picked from _DIRS, never user values.
_WALK = """
WITH RECURSIVE walk(id, depth, via) AS (
    SELECT e.{to}, 1, e.kind FROM graph_edges e
    WHERE e.study_id = :study AND e.{frm} = :node
  UNION
    SELECT e.{to}, w.depth + 1, e.kind FROM graph_edges e JOIN walk w ON e.{frm} = w.id
    WHERE e.study_id = :study AND w.depth < :max_depth
)
SELECT n.id, n.kind, n.ref, n.label, n.props, w.depth, w.via
FROM walk w JOIN graph_nodes n ON n.id = w.id AND n.study_id = :study
ORDER BY w.depth, n.id, w.via
"""
_DIRS = {"up": ("src", "dst"), "down": ("dst", "src")}


def _walk(db: Session, study_id: str, node: str, max_depth: int, direction: str) -> list[dict]:
    frm, to = _DIRS[direction]
    rows = db.execute(text(_WALK.format(frm=frm, to=to)),
                      {"study": study_id, "node": node, "max_depth": max_depth}).all()
    out, seen = [], set()
    for r in rows:  # ordered by depth, so the first sighting is the shallowest
        if r.id in seen:
            continue
        seen.add(r.id)
        out.append({"id": r.id, "kind": r.kind, "ref": r.ref, "label": r.label,
                    "props": json.loads(r.props or "{}"), "depth": r.depth, "via": r.via})
    return out


def lineage(db: Session, study_id: str, node: str, max_depth: int = 12) -> list[dict]:
    """Everything `node` was computed from."""
    return _walk(db, study_id, node, max_depth, "up")


def impact(db: Session, study_id: str, node: str, max_depth: int = 12) -> list[dict]:
    """Everything that depends on `node`."""
    return _walk(db, study_id, node, max_depth, "down")


def delete_study_graph(db: Session, study_id: str) -> int:
    n = db.query(GraphEdgeDB).filter_by(study_id=study_id).delete()
    return n + db.query(GraphNodeDB).filter_by(study_id=study_id).delete()
