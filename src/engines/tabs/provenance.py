"""Where one table cell came from: a short, ordered thread of plain-language steps.

Reads only what is already stored (the computed table, the question graph, the
cleaning decisions, the upload record). Computes nothing new.
"""

import json
import re
from collections import Counter

from sqlalchemy.orm import Session

from api.db import GraphNodeDB
from src.engines.tabs.intervals import interval
from src.platform import graph

# What each routing edge says, from the question's point of view.
_ROUTING = {
    "shown_if": "Shown only to some respondents, depending on the answer to {}",
    "terminates_on": "Screens respondents out depending on the answer to {}",
    "masked_by": "Offers each respondent only some options, based on their answer to {}",
    "piped_from": "Question text uses the answer to {}",
    "derived_from": "Some answers are worked out from {}",
}


def question_node(db: Session, study_id: str, stub: str) -> GraphNodeDB | None:
    """The question node a table stub belongs to: Q3r1 and Q3 both find Q3, and
    AWR_CONSIDERr1 finds AWR_CONSIDER (a multi-select's option column is
    `<question>r<code>`, whatever the question id looks like)."""
    for ref in dict.fromkeys([stub, _base_qid(stub)]):
        node = db.get(GraphNodeDB, graph.node_id(study_id, "question", ref))
        if node is not None:
            return node
    return None


def _base_qid(stub: str) -> str:
    return re.sub(r"r\d+$", "", stub or "")


def _fmt(v) -> str:
    return f"{v:g}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _question_step(db: Session, study_id: str, node: GraphNodeDB) -> dict:
    parts = [f"Type: {json.loads(node.props or '{}').get('type') or 'unknown'}."]
    by_kind: dict[str, list[str]] = {}
    for n in graph.lineage(db, study_id, node.id, max_depth=1):
        by_kind.setdefault(n["via"], []).append(n["ref"])
    for kind, text in _ROUTING.items():
        if kind in by_kind:
            parts.append(text.format(", ".join(by_kind[kind])) + ".")
    if len(parts) == 1:
        parts.append("It is asked of everyone, with no routing, masks or pipes.")
    return {"title": f"Question {node.ref}: {node.label}", "detail": " ".join(parts)}


def provenance(
    db: Session, study_id: str, payload: dict, table: dict, row: int, col: int, *,
    removed: dict[str, str], data_file: dict, survey=None,
) -> dict:
    """Steps behind cell (row, col) of `table`. `removed` is {respondent: reason};
    `data_file` is {name, uploaded_at, uploaded_by, n_rows}, any of them may be missing.
    `survey` is the fallback for the question step when the graph has no node."""
    r, c = table["rows"][row], table["columns"][col]
    value = r["pct"][col]
    eff = c.get("effective_base") or c.get("unweighted_base")
    iv = interval(value, eff)
    shown = (f"{_fmt(value)}% (95% interval {iv[0]:.0f}%–{iv[1]:.0f}%, effective base "
             f"{_fmt(eff)})" if iv else f"{_fmt(value)}%")
    steps = [{
        "title": "The cell",
        "detail": f"{r['label']} among {c['label']}: {shown}. Weighted base "
                  f"{_fmt(c['base'])}, unweighted base {c.get('unweighted_base')}.",
    }]

    flt = table.get("filter")
    steps.append({"title": "The base", "detail": f"Column: {c['label']}. "
                  + (f"Table filter: {flt}." if flt else "No table filter.")})

    node = question_node(db, study_id, table.get("stub", ""))
    if node is not None:
        steps.append(_question_step(db, study_id, node))
        cell = graph.upsert_node(
            db, study_id, "cell", f"{table['id']}:{row}:{col}", f"{r['label']} / {c['label']}",
            {"value": value, "base": c["base"], "unweighted_base": c.get("unweighted_base")})
        graph.link(db, study_id, cell, node.id, "computed_from")
        db.commit()
    elif survey is not None:
        stub = (table.get("stub") or "").upper()
        base = _base_qid(table.get("stub") or "").upper()
        q = next((q for q in survey.questions if q.qid.upper() in (stub, base)), None)
        if q is not None:
            steps.append({"title": f"Question {q.qid}: {q.text}",
                          "detail": f"Type: {getattr(q.type, 'value', q.type)}."})

    if removed:
        why = ", ".join(
            f"{n} {k.replace('_', ' ')}" for k, n in Counter(removed.values()).most_common())
        steps.append({
            "title": "Cleaning",
            "detail": f"{_plural(len(removed), 'respondent')} removed before the tables: {why}."})

    if payload.get("weight"):
        eff, unw = c.get("effective_base"), c.get("unweighted_base")
        detail = f"Weighted by {payload['weight']}."
        if eff and unw:
            detail += (f" Efficiency {eff / unw:.0%} "
                       f"(effective base {_fmt(eff)} of {unw} respondents).")
        steps.append({"title": "Weight", "detail": detail})

    if data_file.get("name"):
        bits = [f"{data_file['name']}"]
        if data_file.get("uploaded_at"):
            bits.append(f"uploaded {data_file['uploaded_at'][:10]}")
        if data_file.get("uploaded_by"):
            bits.append(f"by {data_file['uploaded_by']}")
        if data_file.get("n_rows") is not None:
            bits.append(f"{_plural(data_file['n_rows'], 'row')}")
        steps.append({"title": "The data file", "detail": ", ".join(bits) + "."})

    return {"value": value, "label": r["label"], "column": c["label"], "steps": steps}
