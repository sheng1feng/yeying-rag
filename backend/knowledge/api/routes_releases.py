from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.schemas.future_domain import KBReleaseRead
from knowledge.schemas.releases import (
    ReleaseDetailResponse,
    ReleaseHotfixRequest,
    ReleasePublishRequest,
    ReleaseRollbackRequest,
)
from knowledge.services.release_management import ReleaseManagementService


router = APIRouter(prefix="/kbs", tags=["releases"])
release_management_service = ReleaseManagementService()


def _release_detail(db: Session, release) -> ReleaseDetailResponse:
    items = release_management_service.list_release_items(db, release.id)
    return ReleaseDetailResponse(
        release=KBReleaseRead.model_validate(release),
        items=items,
    )


@router.post("/{kb_id}/releases", response_model=ReleaseDetailResponse)
def publish_release(
    kb_id: int,
    payload: ReleasePublishRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ReleaseDetailResponse:
    try:
        release = release_management_service.publish_workspace_release(
            db,
            wallet_address,
            kb_id,
            version=payload.version,
            release_note=payload.release_note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _release_detail(db, release)


@router.post("/{kb_id}/releases/{release_id}/hotfix", response_model=ReleaseDetailResponse)
def hotfix_release(
    kb_id: int,
    release_id: int,
    payload: ReleaseHotfixRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ReleaseDetailResponse:
    try:
        release = release_management_service.create_hotfix_release(
            db,
            wallet_address,
            kb_id,
            version=payload.version,
            release_note=payload.release_note,
            knowledge_item_ids=payload.knowledge_item_ids,
            base_release_id=release_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _release_detail(db, release)


@router.post("/{kb_id}/releases/{release_id}/rollback", response_model=ReleaseDetailResponse)
def rollback_release(
    kb_id: int,
    release_id: int,
    payload: ReleaseRollbackRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ReleaseDetailResponse:
    try:
        release = release_management_service.rollback_to_release(
            db,
            wallet_address,
            kb_id,
            target_release_id=release_id,
            version=payload.version,
            release_note=payload.release_note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _release_detail(db, release)


@router.get("/{kb_id}/releases", response_model=list[KBReleaseRead])
def list_releases(
    kb_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[KBReleaseRead]:
    try:
        return release_management_service.list_releases(db, wallet_address, kb_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{kb_id}/releases/current", response_model=ReleaseDetailResponse)
def get_current_release(
    kb_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ReleaseDetailResponse:
    try:
        release = release_management_service.get_current_release_or_404(db, wallet_address, kb_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _release_detail(db, release)


@router.get("/{kb_id}/releases/{release_id}", response_model=ReleaseDetailResponse)
def get_release(
    kb_id: int,
    release_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ReleaseDetailResponse:
    try:
        release_management_service._get_kb_or_404(db, wallet_address, kb_id)
        release = release_management_service.get_release_or_404(db, kb_id, release_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _release_detail(db, release)
