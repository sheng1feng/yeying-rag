from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.schemas.evidence import EvidenceBuildResponse
from knowledge.schemas.future_domain import EvidenceUnitRead
from knowledge.services.evidence_pipeline import EvidencePipelineService


router = APIRouter(prefix="/kbs", tags=["evidence"])
evidence_pipeline_service = EvidencePipelineService()


@router.post("/{kb_id}/assets/{asset_id}/build-evidence", response_model=EvidenceBuildResponse)
def build_evidence_for_asset(
    kb_id: int,
    asset_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> EvidenceBuildResponse:
    try:
        stats = evidence_pipeline_service.build_for_asset(db, wallet_address, kb_id, asset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EvidenceBuildResponse(
        kb_id=kb_id,
        asset_id=asset_id,
        processed_asset_count=stats.processed_asset_count,
        built_evidence_count=stats.built_evidence_count,
        skipped_asset_count=stats.skipped_asset_count,
        failed_asset_ids=stats.failed_asset_ids,
    )


@router.post("/{kb_id}/sources/{source_id}/build-evidence", response_model=EvidenceBuildResponse)
def build_evidence_for_source(
    kb_id: int,
    source_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> EvidenceBuildResponse:
    try:
        stats = evidence_pipeline_service.build_for_source(db, wallet_address, kb_id, source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EvidenceBuildResponse(
        kb_id=kb_id,
        source_id=source_id,
        processed_asset_count=stats.processed_asset_count,
        built_evidence_count=stats.built_evidence_count,
        skipped_asset_count=stats.skipped_asset_count,
        failed_asset_ids=stats.failed_asset_ids,
    )


@router.get("/{kb_id}/evidence", response_model=list[EvidenceUnitRead])
def list_evidence(
    kb_id: int,
    source_id: int | None = Query(default=None),
    asset_id: int | None = Query(default=None),
    evidence_type: str | None = Query(default=None),
    vector_status: str | None = Query(default=None),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[EvidenceUnitRead]:
    try:
        return evidence_pipeline_service.list_evidence(
            db,
            wallet_address,
            kb_id,
            source_id=source_id,
            asset_id=asset_id,
            evidence_type=evidence_type,
            vector_status=vector_status,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{kb_id}/evidence/{evidence_id}", response_model=EvidenceUnitRead)
def get_evidence(
    kb_id: int,
    evidence_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> EvidenceUnitRead:
    try:
        return evidence_pipeline_service.get_evidence_or_404(db, wallet_address, kb_id, evidence_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
