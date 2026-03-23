from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.models import KnowledgeBase
from knowledge.schemas.future_domain import KBReleaseRead, ServiceGrantRead, ServicePrincipalRead
from knowledge.schemas.grants import (
    ServiceCurrentReleaseResponse,
    ServiceGrantCreateRequest,
    ServiceGrantResolvedRead,
    ServiceGrantUpdateRequest,
    ServiceKnowledgeBaseRead,
    ServicePrincipalCreateRequest,
    ServicePrincipalCreateResponse,
    ServicePrincipalUpdateRequest,
    ServicePrincipalVerifyRequest,
    ServicePrincipalVerifyResponse,
)
from knowledge.services.release_management import ReleaseManagementService
from knowledge.services.service_grants import ServiceGrantService
from knowledge.services.service_principals import ServicePrincipalService


router = APIRouter(tags=["grants"])
service_principal_service = ServicePrincipalService()
release_management_service = ReleaseManagementService()
service_grant_service = ServiceGrantService(
    principal_service=service_principal_service,
    release_management_service=release_management_service,
)


def _resolved_grant_read(db: Session, grant) -> ServiceGrantResolvedRead:
    release = service_grant_service.resolve_release_for_grant(db, grant)
    kb = db.get(KnowledgeBase, grant.kb_id)
    return ServiceGrantResolvedRead(
        **ServiceGrantRead.model_validate(grant).model_dump(),
        kb_name=kb.name if kb is not None else None,
        resolved_release=KBReleaseRead.model_validate(release),
    )


def _get_service_principal(
    x_service_api_key: str = Header(alias="X-Service-Api-Key"),
    db: Session = Depends(get_db),
):
    try:
        return service_principal_service.verify_api_key(db, x_service_api_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/service-principals", response_model=ServicePrincipalCreateResponse)
def create_service_principal(
    payload: ServicePrincipalCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ServicePrincipalCreateResponse:
    try:
        principal, raw_api_key = service_principal_service.create_principal(
            db,
            wallet_address,
            service_id=payload.service_id,
            display_name=payload.display_name,
            identity_type=payload.identity_type,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ServicePrincipalCreateResponse(
        principal=ServicePrincipalRead.model_validate(principal),
        api_key=raw_api_key,
    )


@router.post("/service-principals/verify", response_model=ServicePrincipalVerifyResponse)
def verify_service_principal(
    payload: ServicePrincipalVerifyRequest,
    db: Session = Depends(get_db),
) -> ServicePrincipalVerifyResponse:
    try:
        principal = service_principal_service.verify_api_key(db, payload.api_key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return ServicePrincipalVerifyResponse(principal=ServicePrincipalRead.model_validate(principal))


@router.get("/service-principals", response_model=list[ServicePrincipalRead])
def list_service_principals(
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[ServicePrincipalRead]:
    try:
        return service_principal_service.list_principals(db, wallet_address)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/service-principals/{principal_id}", response_model=ServicePrincipalRead)
def update_service_principal(
    principal_id: int,
    payload: ServicePrincipalUpdateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ServicePrincipalRead:
    try:
        return service_principal_service.update_principal(
            db,
            wallet_address,
            principal_id,
            display_name=payload.display_name,
            principal_status=payload.principal_status,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/kbs/{kb_id}/grants", response_model=ServiceGrantRead)
def create_service_grant(
    kb_id: int,
    payload: ServiceGrantCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ServiceGrantRead:
    try:
        return service_grant_service.create_grant(
            db,
            wallet_address,
            kb_id,
            service_principal_id=payload.service_principal_id,
            release_selection_mode=payload.release_selection_mode,
            pinned_release_id=payload.pinned_release_id,
            default_result_mode=payload.default_result_mode,
            expires_at=payload.expires_at,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/kbs/{kb_id}/grants", response_model=list[ServiceGrantRead])
def list_service_grants(
    kb_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[ServiceGrantRead]:
    try:
        return service_grant_service.list_grants(db, wallet_address, kb_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/kbs/{kb_id}/grants/{grant_id}", response_model=ServiceGrantRead)
def update_service_grant(
    kb_id: int,
    grant_id: int,
    payload: ServiceGrantUpdateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ServiceGrantRead:
    try:
        return service_grant_service.update_grant(
            db,
            wallet_address,
            kb_id,
            grant_id,
            grant_status=payload.grant_status,
            release_selection_mode=payload.release_selection_mode,
            pinned_release_id=payload.pinned_release_id,
            default_result_mode=payload.default_result_mode,
            expires_at=payload.expires_at,
            revoked_by=payload.revoked_by,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/service/grants", response_model=list[ServiceGrantResolvedRead])
def list_service_grants_for_principal(
    principal=Depends(_get_service_principal),
    db: Session = Depends(get_db),
) -> list[ServiceGrantResolvedRead]:
    grants = service_grant_service.list_grants_for_principal(db, principal.id)
    resolved = []
    for grant in grants:
        try:
            resolved.append(_resolved_grant_read(db, grant))
            service_grant_service.mark_grant_used(db, grant)
        except (LookupError, ValueError):
            resolved.append(
                ServiceGrantResolvedRead(
                    **ServiceGrantRead.model_validate(grant).model_dump(),
                    kb_name=db.get(KnowledgeBase, grant.kb_id).name if db.get(KnowledgeBase, grant.kb_id) is not None else None,
                    resolved_release=None,
                )
            )
    return resolved


@router.get("/service/kbs", response_model=list[ServiceKnowledgeBaseRead])
def list_service_kbs(
    principal=Depends(_get_service_principal),
    db: Session = Depends(get_db),
) -> list[ServiceKnowledgeBaseRead]:
    grants = service_grant_service.list_grants_for_principal(db, principal.id)
    result: list[ServiceKnowledgeBaseRead] = []
    for grant in grants:
        try:
            resolved_grant = _resolved_grant_read(db, grant)
        except (LookupError, ValueError):
            continue
        service_grant_service.mark_grant_used(db, grant)
        result.append(
            ServiceKnowledgeBaseRead(
                kb_id=grant.kb_id,
                kb_name=resolved_grant.kb_name or "",
                grant=resolved_grant,
            )
        )
    return result


@router.get("/service/releases/current", response_model=ServiceCurrentReleaseResponse)
def get_service_current_release(
    kb_id: int = Query(),
    principal=Depends(_get_service_principal),
    db: Session = Depends(get_db),
) -> ServiceCurrentReleaseResponse:
    grants = service_grant_service.list_grants_for_principal(db, principal.id)
    grant = next((item for item in grants if item.kb_id == kb_id), None)
    if grant is None:
        raise HTTPException(status_code=404, detail="service grant not found for knowledge base")
    try:
        resolved_grant = _resolved_grant_read(db, grant)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    service_grant_service.mark_grant_used(db, grant)
    return ServiceCurrentReleaseResponse(
        kb_id=kb_id,
        release=resolved_grant.resolved_release,
        grant=resolved_grant,
    )
