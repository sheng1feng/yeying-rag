from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.schemas.future_domain import KBReleaseRead, RetrievalLogRead
from knowledge.schemas.search_lab import SearchLabCompareRequest, SearchLabCompareResponse, SourceGovernanceAssetRead, SourceGovernanceResponse, SourceGovernanceSourceRead
from knowledge.services.search_lab import SearchLabService


router = APIRouter(prefix="/kbs", tags=["search_lab"])
search_lab_service = SearchLabService()


@router.post("/{kb_id}/search-lab/compare", response_model=SearchLabCompareResponse)
def compare_search_modes(
    kb_id: int,
    payload: SearchLabCompareRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> SearchLabCompareResponse:
    try:
        release, formal_only, evidence_only, formal_first, log = search_lab_service.compare(
            db,
            wallet_address,
            kb_id,
            query=payload.query,
            top_k=payload.top_k,
            result_view=payload.result_view,
            availability_mode=payload.availability_mode,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SearchLabCompareResponse(
        kb_id=kb_id,
        query=payload.query,
        current_release=KBReleaseRead.model_validate(release) if release is not None else None,
        retrieval_log_id=log.id,
        formal_only=formal_only,
        evidence_only=evidence_only,
        formal_first=formal_first,
    )


@router.get("/{kb_id}/retrieval-logs", response_model=list[RetrievalLogRead])
def list_retrieval_logs(
    kb_id: int,
    limit: int = Query(default=50),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[RetrievalLogRead]:
    try:
        logs = search_lab_service.list_retrieval_logs(db, wallet_address, kb_id, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [RetrievalLogRead.model_validate(log) for log in logs]


@router.get("/{kb_id}/retrieval-logs/{log_id}", response_model=RetrievalLogRead)
def get_retrieval_log(
    kb_id: int,
    log_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> RetrievalLogRead:
    try:
        log = search_lab_service.get_retrieval_log_or_404(db, wallet_address, kb_id, log_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RetrievalLogRead.model_validate(log)


@router.get("/{kb_id}/source-governance", response_model=SourceGovernanceResponse)
def get_source_governance(
    kb_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> SourceGovernanceResponse:
    try:
        payload = search_lab_service.source_governance(db, wallet_address, kb_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SourceGovernanceResponse(
        kb_id=payload["kb_id"],
        status_counts=payload["status_counts"],
        sources=[
            SourceGovernanceSourceRead(
                source_id=item["source_id"],
                source_path=item["source_path"],
                sync_status=item["sync_status"],
                affected_assets=[SourceGovernanceAssetRead(**asset) for asset in item["affected_assets"]],
            )
            for item in payload["sources"]
        ],
        assets=[SourceGovernanceAssetRead(**asset) for asset in payload["assets"]],
    )
