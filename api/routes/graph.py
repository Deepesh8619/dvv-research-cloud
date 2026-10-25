"""Lineage and impact over a study's graph."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.db import GraphNodeDB, UserDB, get_db, get_study
from src.platform import graph

router = APIRouter()


def _node(db: Session, study_id: str, node: str) -> str:
    if not get_study(db, study_id):
        raise HTTPException(status_code=404, detail="Study not found")
    kind, _, ref = node.partition(":")
    nid = graph.node_id(study_id, kind, ref)
    if not (kind and ref) or db.get(GraphNodeDB, nid) is None:
        raise HTTPException(status_code=404, detail="That item is not in this study's graph")
    return nid


@router.get("/studies/{study_id}/graph/lineage")
def lineage(study_id: str, node: str, db: Session = Depends(get_db),
            user: UserDB = Depends(get_current_user)):
    """Everything `node` ("<kind>:<ref>") was computed from. (Authenticated)"""
    return graph.lineage(db, study_id, _node(db, study_id, node))


@router.get("/studies/{study_id}/graph/impact")
def impact(study_id: str, node: str, db: Session = Depends(get_db),
           user: UserDB = Depends(get_current_user)):
    """Everything that depends on `node` ("<kind>:<ref>"). (Authenticated)"""
    return graph.impact(db, study_id, _node(db, study_id, node))
