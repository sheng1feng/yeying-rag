from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
import httpx
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.schemas.future_domain import SourceCreateRequest, SourceRead
from knowledge.schemas.sources import SourceScanResponse, SourceScanStatsResponse, SourceUpdateRequest
from knowledge.services.asset_inventory import SourceScopeMismatchError
from knowledge.services.source_registry import SourceRegistryService
from knowledge.services.source_sync import SourceSyncService
from knowledge.utils.time import utc_now


router = APIRouter(prefix="/kbs", tags=["sources"])
source_registry_service = SourceRegistryService()
source_sync_service = SourceSyncService(source_registry_service=source_registry_service)


@router.post("/{kb_id}/sources", response_model=SourceRead)
def create_source(
    kb_id: int,
    payload: SourceCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> SourceRead:
    try:
        return source_registry_service.create_source(
            db=db,
            wallet_address=wallet_address,
            kb_id=kb_id,
            source_type=payload.source_type,
            source_path=payload.source_path,
            scope_type=payload.scope_type,
            enabled=payload.enabled,
            missing_policy=payload.missing_policy,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{kb_id}/sources", response_model=list[SourceRead])
def list_sources(
    kb_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[SourceRead]:
    try:
        return source_registry_service.list_sources(db, wallet_address, kb_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{kb_id}/sources/{source_id}", response_model=SourceRead)
def get_source(
    kb_id: int,
    source_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> SourceRead:
    try:
        return source_registry_service.get_source_or_404(db, wallet_address, kb_id, source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{kb_id}/sources/{source_id}", response_model=SourceRead)
def update_source(
    kb_id: int,
    source_id: int,
    payload: SourceUpdateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> SourceRead:
    try:
        return source_registry_service.update_source(
            db,
            wallet_address,
            kb_id,
            source_id,
            enabled=payload.enabled,
            missing_policy=payload.missing_policy,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{kb_id}/sources/{source_id}/scan", response_model=SourceScanResponse)
def scan_source(
    kb_id: int,
    source_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> SourceScanResponse:
    try:
        source, stats = source_sync_service.scan_source(db, wallet_address, kb_id, source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SourceScopeMismatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SourceScanResponse(
        source=SourceRead.model_validate(source),
        stats=SourceScanStatsResponse(
            total_assets=stats.total_assets,
            discovered_assets=stats.discovered_assets,
            available_assets=stats.available_assets,
            changed_assets=stats.changed_assets,
            missing_assets=stats.missing_assets,
            ignored_assets=stats.ignored_assets,
            scanned_at=source.last_synced_at or utc_now(),
        ),
    )
