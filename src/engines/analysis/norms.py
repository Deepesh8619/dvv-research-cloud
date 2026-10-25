"""Norms: the range a question's answers take across the agency's past studies.

A past study's table counts when its question's vector (project_chunks, same
embedding model) is within MIN_COSINE of this one's, its row labels cover at
least MIN_OVERLAP of this table's, and its tables are processed.
"""

import json
import re
import statistics

import numpy as np
from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.db import ProjectChunkDB, StudyDB

MIN_COSINE = 0.90
MIN_OVERLAP = 0.80
MIN_STUDIES = 2
NOT_ENOUGH = "Not enough past studies yet"


def _unit(v: list[float]) -> np.ndarray:
    a = np.array(v, dtype=float)
    return a / max(float(np.linalg.norm(a)), 1e-12)


def _total(table: dict) -> int:
    cols = table["columns"]
    return next((i for i, c in enumerate(cols) if c["label"].strip().lower() == "total"), 0)


def norms_for(db: Session, study_id: str, table: dict) -> dict | None:
    from api.routes.studies import load_crosstabs  # the one table loader
    from src.engines.analysis.findings import _option

    stub = table.get("stub") or ""
    # One option of a multi-select (Q5r3): every option table of that question has
    # the same Checked / Not checked rows, so the option itself must match too.
    option = (_option(table.get("title") or "").strip().lower()
              if re.search(r"r\d+$", stub) else None)
    refs = dict.fromkeys([stub, re.sub(r"r\d+$", "", stub)])
    chunks = (db.get(ProjectChunkDB, f"{study_id}:question:{ref}") for ref in refs)
    mine = next((c for c in chunks if c and c.vector_json), None)
    labels = {r["label"].strip().lower() for r in table["rows"]}
    if mine is None or not labels:
        return None
    qv = _unit(json.loads(mine.vector_json))

    # ponytail: scans every past question chunk; add a per-model pgvector query if it grows large
    best: dict[str, str] = {}  # past study -> its best-matching question ref
    best_score: dict[str, float] = {}
    for c in db.query(ProjectChunkDB).filter(
            ProjectChunkDB.kind == "question", ProjectChunkDB.model == mine.model,
            ProjectChunkDB.study_id != study_id).all():
        v = json.loads(c.vector_json or "[]")
        if len(v) != len(qv):
            continue
        score = float(_unit(v) @ qv)
        if score >= MIN_COSINE and score > best_score.get(c.study_id, 0):
            best[c.study_id], best_score[c.study_id] = c.ref, score

    matched, values = [], {}
    for sid, ref in sorted(best.items()):
        study = db.get(StudyDB, sid)
        try:
            payload = load_crosstabs(study) if study else None
        except HTTPException:
            continue
        for t in (payload or {}).get("tables", []):
            if (t.get("stub") or "") != ref and re.sub(r"r\d+$", "", t.get("stub") or "") != ref:
                continue
            if option is not None and _option(t.get("title") or "").strip().lower() != option:
                continue
            theirs = {r["label"].strip().lower(): r for r in t["rows"]}
            if len(labels & theirs.keys()) / len(labels) < MIN_OVERLAP:
                continue
            col = _total(t)
            title = payload.get("title") or study.client_name or sid
            matched.append({"study_id": sid, "title": title})
            for lab in labels & theirs.keys():
                values.setdefault(lab, []).append(theirs[lab]["pct"][col])
            break
    if len(matched) < MIN_STUDIES:
        return None
    rows = []
    for r in table["rows"]:
        v = values.get(r["label"].strip().lower(), [])
        if v:
            rows.append({"label": r["label"], "n_studies": len(v), "min": min(v),
                         "median": statistics.median(v), "max": max(v)})
    return {"rows": rows, "matched": matched}
