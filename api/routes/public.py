"""Public endpoints for share links (no auth required)."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from api.db import (
    get_db,
    get_share_link,
    get_study,
)
from api.models import PublicStudyResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/public/studies/{token}", response_model=PublicStudyResponse)
def get_public_study(token: str, db: Session = Depends(get_db)):
    """Get study metadata via public share link (thin payload)."""
    # Verify share link exists and is not revoked
    link = get_share_link(db, token)
    if not link or link.revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share link not found or has been revoked",
        )

    # Get study
    study = get_study(db, link.study_id)
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study not found",
        )

    # Only serve if study is approved for delivery or already delivered
    if study.status not in ("approved_for_delivery", "delivered"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Study is not yet approved for delivery",
        )

    # Return thin payload
    return PublicStudyResponse(
        client_name=study.client_name,
        status=study.status,
        created_at=study.created_at,
        excel_available=bool(study.excel_path and Path(study.excel_path).exists()),
        pptx_available=bool(study.pptx_path and Path(study.pptx_path).exists()),
    )


@router.get("/public/studies/{token}/download/{fmt}")
def download_public_study(token: str, fmt: str, db: Session = Depends(get_db)):
    """Download study reports via public share link (no auth required)."""
    # Verify share link exists and is not revoked
    link = get_share_link(db, token)
    if not link or link.revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share link not found or has been revoked",
        )

    # Get study
    study = get_study(db, link.study_id)
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study not found",
        )

    # Only serve if study is approved for delivery or already delivered
    if study.status not in ("approved_for_delivery", "delivered"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Study is not yet approved for delivery",
        )

    # Determine which file to serve
    if fmt == "excel":
        path = study.excel_path
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "pptx":
        path = study.pptx_path
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format must be 'excel' or 'pptx'",
        )

    if not path or not Path(path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {fmt} deck available",
        )

    return FileResponse(path, media_type=media, filename=Path(path).name)


@router.get("/public/studies/{token}/crosstabs")
def get_public_crosstabs(token: str, db: Session = Depends(get_db)):
    """Computed tables via public share link. Same gate as the downloads."""
    link = get_share_link(db, token)
    if not link or link.revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share link not found or has been revoked",
        )
    study = get_study(db, link.study_id)
    if not study:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found")
    if study.status not in ("approved_for_delivery", "delivered"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Study is not yet approved for delivery",
        )

    raw = None
    if study.excel_path:
        f = Path(study.excel_path).parent / "crosstabs.json"
        if f.exists():
            raw = f.read_text()
    raw = raw or getattr(study, "crosstabs_json", None)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No table data for this study"
        )

    # Whitelist top-level keys: nothing internal can ride along.
    data = json.loads(raw)
    keys = ("title", "weight", "confidence", "min_base_for_test", "tables")
    return {k: data[k] for k in keys if k in data}
