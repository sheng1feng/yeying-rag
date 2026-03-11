from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.schemas.memory import (
    LongTermMemoryCreateRequest,
    MemoryIngestionEventResponse,
    MemoryIngestionRequest,
    MemoryIngestionResponse,
    LongTermMemoryResponse,
    LongTermMemoryUpdateRequest,
    ShortTermMemoryCreateRequest,
    ShortTermMemoryResponse,
)
from knowledge.services.memory_ingestion import MemoryIngestionService
from knowledge.services.memory import MemoryService


router = APIRouter(prefix="/memory", tags=["memory"])
service = MemoryService()
ingestion_service = MemoryIngestionService()


@router.get("/long-term", response_model=list[LongTermMemoryResponse])
def list_long_term(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)):
    return service.list_long_term(db, wallet_address)


@router.post("/long-term", response_model=LongTermMemoryResponse)
def create_long_term(payload: LongTermMemoryCreateRequest, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)):
    return service.create_long_term(db, wallet_address, payload.model_dump())


@router.patch("/long-term/{memory_id}", response_model=LongTermMemoryResponse)
def update_long_term(
    memory_id: int,
    payload: LongTermMemoryUpdateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
):
    try:
        return service.update_long_term(db, wallet_address, memory_id, payload.model_dump(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/long-term/{memory_id}")
def delete_long_term(memory_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)):
    try:
        service.delete_long_term(db, wallet_address, memory_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/short-term", response_model=list[ShortTermMemoryResponse])
def list_short_term(session_id: str | None = None, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)):
    return service.list_short_term(db, wallet_address, session_id=session_id)


@router.post("/short-term", response_model=ShortTermMemoryResponse)
def create_short_term(payload: ShortTermMemoryCreateRequest, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)):
    return service.create_short_term(db, wallet_address, payload.model_dump())


@router.delete("/short-term/{memory_id}")
def delete_short_term(memory_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)):
    try:
        service.delete_short_term(db, wallet_address, memory_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/ingestions", response_model=list[MemoryIngestionEventResponse])
def list_ingestions(
    session_id: str | None = None,
    limit: int = 20,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
):
    return ingestion_service.list_events(db, wallet_address, session_id=session_id, limit=limit)


@router.post("/ingest", response_model=MemoryIngestionResponse)
def ingest_memory(payload: MemoryIngestionRequest, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)):
    try:
        return ingestion_service.ingest(db, wallet_address, payload.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
