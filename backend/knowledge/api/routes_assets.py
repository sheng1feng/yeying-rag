from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.models import Source, SourceAsset
from knowledge.schemas.future_domain import SourceAssetRead
from knowledge.services.source_registry import SourceRegistryService


router = APIRouter(prefix="/kbs", tags=["assets"])
source_registry_service = SourceRegistryService()


def _list_assets(
    db: Session,
    wallet_address: str,
    kb_id: int,
    *,
    source_id: int | None = None,
    availability_status: str | None = None,
) -> list[SourceAsset]:
    source_registry_service.get_kb_or_404(db, wallet_address, kb_id)
    stmt = (
        select(SourceAsset)
        .where(SourceAsset.kb_id == kb_id)
        .order_by(SourceAsset.source_id.asc(), SourceAsset.asset_path.asc(), SourceAsset.id.asc())
    )
    if source_id is not None:
        source = db.get(Source, source_id)
        if source is None or source.kb_id != kb_id:
            raise LookupError("source not found")
        stmt = stmt.where(SourceAsset.source_id == source_id)
    if availability_status:
        stmt = stmt.where(SourceAsset.availability_status == availability_status)
    return list(db.scalars(stmt).all())


@router.get("/{kb_id}/assets", response_model=list[SourceAssetRead])
def list_assets(
    kb_id: int,
    source_id: int | None = Query(default=None),
    availability_status: str | None = Query(default=None),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[SourceAssetRead]:
    try:
        return _list_assets(
            db,
            wallet_address,
            kb_id,
            source_id=source_id,
            availability_status=availability_status,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{kb_id}/sources/{source_id}/assets", response_model=list[SourceAssetRead])
def list_source_assets(
    kb_id: int,
    source_id: int,
    availability_status: str | None = Query(default=None),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[SourceAssetRead]:
    try:
        return _list_assets(
            db,
            wallet_address,
            kb_id,
            source_id=source_id,
            availability_status=availability_status,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
