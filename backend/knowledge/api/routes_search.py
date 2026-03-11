from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.schemas.search import RetrievalContextRequest, RetrievalContextResponse, SearchRequest
from knowledge.services.retrieval import RetrievalService


router = APIRouter(tags=["search", "retrieval_context"])
service = RetrievalService()


@router.post("/kbs/{kb_id}/search")
def search_kb(kb_id: int, payload: SearchRequest, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)):
    try:
        return service.search(db, wallet_address, [kb_id], payload.query, top_k=payload.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/retrieval-context", response_model=RetrievalContextResponse)
def retrieval_context(
    payload: RetrievalContextRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
):
    try:
        return service.build_retrieval_context(
            db,
            wallet_address=wallet_address,
            session_id=payload.session_id,
            kb_ids=payload.kb_ids,
            query=payload.query,
            top_k=payload.top_k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
