"""Lifecycle rail: where a study stands and what needs a person."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.db import UserDB, get_db, get_study
from src.engines.lifecycle import lifecycle

router = APIRouter()


@router.get("/studies/{study_id}/lifecycle")
def get_lifecycle(study_id: str, db: Session = Depends(get_db),
                  user: UserDB = Depends(get_current_user)):
    """Eight stage states plus the items that need you. (Authenticated)"""
    study = get_study(db, study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    return lifecycle(db, study)
