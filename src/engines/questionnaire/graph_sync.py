"""Write a questionnaire's dependencies into the study graph.

An edge points from the question that depends to the question it depends on.
"""

from sqlalchemy.orm import Session

from api.db import GraphEdgeDB, GraphNodeDB
from src.core.survey_definition import Question, SurveyDefinition
from src.platform import graph

_KINDS = ("shown_if", "terminates_on", "masked_by", "piped_from", "derived_from")


def _deps(q: Question) -> list[tuple[str, str]]:
    """(edge kind, source qid) for everything `q` depends on."""
    out: list[tuple[str, str]] = []
    if q.show_if:
        out += [("shown_if", r) for r in q.show_if.referenced_qids()]
    if q.terminate_if:
        out += [("terminates_on", r) for r in q.terminate_if.referenced_qids()]
    tech = (q.validation or {}).get("techniques") or {}
    for m in tech.get("masks") or []:
        out += [("masked_by", m[k]) for k in ("source_qid", "exclude_qid") if m.get(k)]
    out += [("piped_from", p["source_qid"]) for p in tech.get("pipes") or [] if p.get("source_qid")]
    for o in q.options or []:
        if o.derive_if:
            out += [("derived_from", r) for r in o.derive_if.referenced_qids()]
    return out


def sync_questionnaire(db: Session, study_id: str, survey: SurveyDefinition) -> dict:
    """Make the study's question nodes and dependency edges match `survey`. No commit."""
    block_of = {qid: b.block_id for b in survey.blocks for qid in b.question_ids}
    by_upper = {q.qid.upper(): q.qid for q in survey.questions}  # masks store qids upper-cased
    ids = {q.qid: graph.node_id(study_id, "question", q.qid) for q in survey.questions}

    stale = [n.id for n in db.query(GraphNodeDB).filter_by(study_id=study_id, kind="question")
             if n.ref not in ids]
    if stale:
        db.query(GraphEdgeDB).filter(GraphEdgeDB.study_id == study_id,
                                     GraphEdgeDB.src.in_(stale)).delete(synchronize_session=False)
        db.query(GraphEdgeDB).filter(GraphEdgeDB.study_id == study_id,
                                     GraphEdgeDB.dst.in_(stale)).delete(synchronize_session=False)
        db.query(GraphNodeDB).filter(GraphNodeDB.id.in_(stale)).delete(synchronize_session=False)

    edges = 0
    for q in survey.questions:
        graph.upsert_node(db, study_id, "question", q.qid, (q.text or "")[:200],
                          {"type": getattr(q.type, "value", q.type), "block": block_of.get(q.qid)})
        for kind in _KINDS:
            graph.unlink_kind(db, study_id, ids[q.qid], kind)
    db.flush()
    for q in survey.questions:
        seen = set()
        for kind, ref in _deps(q):
            dst = by_upper.get(ref.upper())
            if dst is None or (kind, dst) in seen:
                continue  # a qid not in the survey has no node to point at
            seen.add((kind, dst))
            graph.link(db, study_id, ids[q.qid], ids[dst], kind)
            edges += 1
    return {"nodes": len(ids), "edges": edges}
