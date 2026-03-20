from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from knowledge.db.session import get_db
from knowledge.schemas.service_search import ServiceSearchRequest, ServiceSearchResponse
from knowledge.services.service_search import ServiceSearchService


router = APIRouter(tags=["service_search"])
service_search_service = ServiceSearchService()


def _search(mode: str, payload: ServiceSearchRequest, service_api_key: str, db: Session) -> ServiceSearchResponse:
    try:
        return service_search_service.search(
            db,
            service_api_key=service_api_key,
            kb_id=payload.kb_id,
            query=payload.query,
            mode=mode,
            result_view=payload.result_view,
            availability_mode=payload.availability_mode,
            top_k=payload.top_k,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400 if "must be one of" in str(exc) or "cannot" in str(exc) else 403, detail=str(exc)) from exc


@router.post("/service/search", response_model=ServiceSearchResponse)
def service_search(
    payload: ServiceSearchRequest,
    x_service_api_key: str = Header(alias="X-Service-Api-Key"),
    db: Session = Depends(get_db),
):
    return _search("formal_first", payload, x_service_api_key, db)


@router.post("/service/search/formal", response_model=ServiceSearchResponse)
def service_search_formal(
    payload: ServiceSearchRequest,
    x_service_api_key: str = Header(alias="X-Service-Api-Key"),
    db: Session = Depends(get_db),
):
    return _search("formal_only", payload, x_service_api_key, db)


@router.post("/service/search/evidence", response_model=ServiceSearchResponse)
def service_search_evidence(
    payload: ServiceSearchRequest,
    x_service_api_key: str = Header(alias="X-Service-Api-Key"),
    db: Session = Depends(get_db),
):
    return _search("evidence_only", payload, x_service_api_key, db)
