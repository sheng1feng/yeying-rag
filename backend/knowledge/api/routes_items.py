from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.schemas.future_domain import KnowledgeItemCandidateRead, KnowledgeItemRead
from knowledge.schemas.items import (
    CandidateAcceptRequest,
    CandidateGenerationResponse,
    CandidateRejectRequest,
    KnowledgeItemDetailResponse,
    KnowledgeItemRevisionDetailRead,
    ManualItemCreateRequest,
    ManualItemUpdateRequest,
)
from knowledge.schemas.future_domain import KnowledgeItemEvidenceLinkRead
from knowledge.services.candidate_extraction import CandidateExtractionService
from knowledge.services.item_contracts import ItemContractValidationError
from knowledge.services.knowledge_items import KnowledgeItemsService, KnowledgeItemValidationService


router = APIRouter(prefix="/kbs", tags=["items"])
candidate_extraction_service = CandidateExtractionService()
knowledge_items_service = KnowledgeItemsService()
knowledge_item_validation_service = KnowledgeItemValidationService()


def _revision_detail(revision, evidence_links):
    revision_payload = KnowledgeItemRevisionDetailRead.model_validate(revision).model_dump(exclude={"evidence_links"})
    return KnowledgeItemRevisionDetailRead(
        **revision_payload,
        evidence_links=[KnowledgeItemEvidenceLinkRead.model_validate(link) for link in evidence_links],
    )


def _raise_contract_error(exc: ItemContractValidationError) -> None:
    failure = knowledge_item_validation_service.build_failure_payload(exc)
    raise HTTPException(status_code=400, detail=failure.model_dump())


@router.post("/{kb_id}/assets/{asset_id}/generate-candidates", response_model=CandidateGenerationResponse)
def generate_candidates_for_asset(
    kb_id: int,
    asset_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> CandidateGenerationResponse:
    try:
        result = candidate_extraction_service.generate_for_asset(db, kb_id, asset_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ItemContractValidationError as exc:
        _raise_contract_error(exc)
    return CandidateGenerationResponse(
        kb_id=kb_id,
        asset_id=asset_id,
        created_count=result.created_count,
        reused_count=result.reused_count,
        candidates=[KnowledgeItemCandidateRead.model_validate(candidate) for candidate in result.candidates],
    )


@router.post("/{kb_id}/sources/{source_id}/generate-candidates", response_model=CandidateGenerationResponse)
def generate_candidates_for_source(
    kb_id: int,
    source_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> CandidateGenerationResponse:
    try:
        result = candidate_extraction_service.generate_for_source(db, kb_id, source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ItemContractValidationError as exc:
        _raise_contract_error(exc)
    return CandidateGenerationResponse(
        kb_id=kb_id,
        source_id=source_id,
        created_count=result.created_count,
        reused_count=result.reused_count,
        candidates=[KnowledgeItemCandidateRead.model_validate(candidate) for candidate in result.candidates],
    )


@router.get("/{kb_id}/candidates", response_model=list[KnowledgeItemCandidateRead])
def list_candidates(
    kb_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[KnowledgeItemCandidateRead]:
    try:
        return knowledge_items_service.list_candidates(db, wallet_address, kb_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{kb_id}/candidates/{candidate_id}/accept", response_model=KnowledgeItemDetailResponse)
def accept_candidate(
    kb_id: int,
    candidate_id: int,
    payload: CandidateAcceptRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> KnowledgeItemDetailResponse:
    try:
        result = knowledge_items_service.accept_candidate(
            db,
            wallet_address,
            kb_id,
            candidate_id,
            title=payload.title,
            statement=payload.statement,
            item_type=payload.item_type,
            structured_payload_json=payload.structured_payload_json,
            item_contract_version=payload.item_contract_version,
            evidence_unit_ids=payload.evidence_unit_ids,
            source_note=payload.source_note,
            applicability_scope_json=payload.applicability_scope_json,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ItemContractValidationError as exc:
        _raise_contract_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item, current_revision, revisions = knowledge_items_service.get_item_detail(db, wallet_address, kb_id, result.item.id)
    return KnowledgeItemDetailResponse(
        item=KnowledgeItemRead.model_validate(item),
        current_revision=_revision_detail(current_revision, list(current_revision.evidence_links or [])) if current_revision is not None else None,
        revisions=[_revision_detail(revision, evidence_links) for revision, evidence_links in revisions],
    )


@router.post("/{kb_id}/candidates/{candidate_id}/reject", response_model=KnowledgeItemCandidateRead)
def reject_candidate(
    kb_id: int,
    candidate_id: int,
    payload: CandidateRejectRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> KnowledgeItemCandidateRead:
    try:
        return knowledge_items_service.reject_candidate(
            db,
            wallet_address,
            kb_id,
            candidate_id,
            source_note=payload.source_note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{kb_id}/items", response_model=list[KnowledgeItemRead])
def list_items(
    kb_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[KnowledgeItemRead]:
    try:
        return knowledge_items_service.list_items(db, wallet_address, kb_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{kb_id}/items/manual", response_model=KnowledgeItemDetailResponse)
def create_manual_item(
    kb_id: int,
    payload: ManualItemCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> KnowledgeItemDetailResponse:
    try:
        result = knowledge_items_service.create_manual_item(
            db,
            wallet_address,
            kb_id,
            title=payload.title,
            statement=payload.statement,
            item_type=payload.item_type,
            structured_payload_json=payload.structured_payload_json,
            item_contract_version=payload.item_contract_version,
            evidence_unit_ids=payload.evidence_unit_ids,
            source_note=payload.source_note,
            applicability_scope_json=payload.applicability_scope_json,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ItemContractValidationError as exc:
        _raise_contract_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item, current_revision, revisions = knowledge_items_service.get_item_detail(db, wallet_address, kb_id, result.item.id)
    return KnowledgeItemDetailResponse(
        item=KnowledgeItemRead.model_validate(item),
        current_revision=_revision_detail(current_revision, list(current_revision.evidence_links or [])) if current_revision is not None else None,
        revisions=[_revision_detail(revision, evidence_links) for revision, evidence_links in revisions],
    )


@router.get("/{kb_id}/items/{item_id}", response_model=KnowledgeItemDetailResponse)
def get_item(
    kb_id: int,
    item_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> KnowledgeItemDetailResponse:
    try:
        item, current_revision, revisions = knowledge_items_service.get_item_detail(db, wallet_address, kb_id, item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return KnowledgeItemDetailResponse(
        item=KnowledgeItemRead.model_validate(item),
        current_revision=_revision_detail(current_revision, list(current_revision.evidence_links or [])) if current_revision is not None else None,
        revisions=[_revision_detail(revision, evidence_links) for revision, evidence_links in revisions],
    )


@router.patch("/{kb_id}/items/{item_id}", response_model=KnowledgeItemDetailResponse)
def update_item(
    kb_id: int,
    item_id: int,
    payload: ManualItemUpdateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> KnowledgeItemDetailResponse:
    try:
        result = knowledge_items_service.update_manual_item(
            db,
            wallet_address,
            kb_id,
            item_id,
            title=payload.title,
            statement=payload.statement,
            item_type=payload.item_type,
            structured_payload_json=payload.structured_payload_json,
            item_contract_version=payload.item_contract_version,
            evidence_unit_ids=payload.evidence_unit_ids,
            source_note=payload.source_note,
            applicability_scope_json=payload.applicability_scope_json,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ItemContractValidationError as exc:
        _raise_contract_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item, current_revision, revisions = knowledge_items_service.get_item_detail(db, wallet_address, kb_id, result.item.id)
    return KnowledgeItemDetailResponse(
        item=KnowledgeItemRead.model_validate(item),
        current_revision=_revision_detail(current_revision, list(current_revision.evidence_links or [])) if current_revision is not None else None,
        revisions=[_revision_detail(revision, evidence_links) for revision, evidence_links in revisions],
    )
