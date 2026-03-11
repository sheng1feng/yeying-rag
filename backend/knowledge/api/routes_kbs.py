from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.core.settings import get_settings
from knowledge.db.session import get_db
from knowledge.models import (
    EmbeddingRecord,
    ImportedChunk,
    ImportedDocument,
    ImportTask,
    ImportTaskItem,
    KnowledgeBase,
    LongTermMemory,
    MemoryIngestionEvent,
    SourceBinding,
)
from knowledge.schemas.kb import KBCreateRequest, KBResponse, KBStatsResponse, KBUpdateRequest
from knowledge.services.ingestion import IngestionService


router = APIRouter(prefix="/kbs", tags=["knowledge_bases"])
settings = get_settings()
ingestion_service = IngestionService()


def _default_kb_config() -> dict:
    return {
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "retrieval_top_k": settings.retrieval_top_k,
        "memory_top_k": settings.memory_top_k,
        "embedding_model": settings.embedding_model,
    }


def _normalize_kb_config(config: dict | None) -> dict:
    return {**_default_kb_config(), **(config or {})}


def _chunking_config_changed(previous: dict, current: dict) -> bool:
    return any(previous.get(key) != current.get(key) for key in ("chunk_size", "chunk_overlap", "embedding_model"))


def _schedule_reindex_if_needed(db: Session, wallet_address: str, kb_id: int) -> None:
    source_paths = list(
        db.scalars(
            select(ImportedDocument.source_path)
            .where(ImportedDocument.kb_id == kb_id)
            .where(ImportedDocument.owner_wallet_address == wallet_address)
        ).all()
    )
    if not source_paths:
        return
    pending_task = db.scalar(
        select(ImportTask)
        .where(ImportTask.kb_id == kb_id)
        .where(ImportTask.owner_wallet_address == wallet_address)
        .where(ImportTask.task_type == "reindex")
        .where(ImportTask.status.in_(("pending", "running")))
        .order_by(ImportTask.created_at.desc())
    )
    if pending_task is not None:
        return
    ingestion_service.create_task(
        db=db,
        wallet_address=wallet_address,
        kb_id=kb_id,
        task_type="reindex",
        source_paths=list(dict.fromkeys(source_paths)),
    )


def _delete_kb_resources(db: Session, wallet_address: str, kb_id: int) -> None:
    active_task = db.scalar(
        select(ImportTask)
        .where(ImportTask.kb_id == kb_id)
        .where(ImportTask.owner_wallet_address == wallet_address)
        .where(ImportTask.status.in_(("running", "cancel_requested")))
        .order_by(ImportTask.created_at.desc())
    )
    if active_task is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"knowledge base has active task #{active_task.id} ({active_task.task_type}); cancel or wait for completion first",
        )

    vector_ids = list(
        db.scalars(
            select(EmbeddingRecord.vector_id)
            .where(EmbeddingRecord.kb_id == kb_id)
            .where(EmbeddingRecord.owner_wallet_address == wallet_address)
        ).all()
    )
    ingestion_service.vector_store.delete_vectors([vector_id for vector_id in vector_ids if vector_id])

    task_ids = select(ImportTask.id).where(ImportTask.kb_id == kb_id).where(ImportTask.owner_wallet_address == wallet_address)
    db.execute(delete(ImportTaskItem).where(ImportTaskItem.task_id.in_(task_ids)))
    db.execute(delete(ImportTask).where(ImportTask.kb_id == kb_id).where(ImportTask.owner_wallet_address == wallet_address))
    db.execute(delete(MemoryIngestionEvent).where(MemoryIngestionEvent.kb_id == kb_id).where(MemoryIngestionEvent.owner_wallet_address == wallet_address))
    db.execute(delete(LongTermMemory).where(LongTermMemory.kb_id == kb_id).where(LongTermMemory.owner_wallet_address == wallet_address))
    db.execute(delete(EmbeddingRecord).where(EmbeddingRecord.kb_id == kb_id).where(EmbeddingRecord.owner_wallet_address == wallet_address))
    db.execute(delete(ImportedChunk).where(ImportedChunk.kb_id == kb_id).where(ImportedChunk.owner_wallet_address == wallet_address))
    db.execute(delete(ImportedDocument).where(ImportedDocument.kb_id == kb_id).where(ImportedDocument.owner_wallet_address == wallet_address))
    db.execute(delete(SourceBinding).where(SourceBinding.kb_id == kb_id))


def _get_kb_or_404(db: Session, wallet_address: str, kb_id: int) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")
    return kb


@router.get("", response_model=list[KBResponse])
def list_kbs(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> list[KnowledgeBase]:
    return list(
        db.scalars(
            select(KnowledgeBase)
            .where(KnowledgeBase.owner_wallet_address == wallet_address)
            .order_by(KnowledgeBase.created_at.desc())
        ).all()
    )


@router.post("", response_model=KBResponse)
def create_kb(payload: KBCreateRequest, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> KnowledgeBase:
    config = _normalize_kb_config(payload.retrieval_config.model_dump() if payload.retrieval_config else None)
    kb = KnowledgeBase(
        owner_wallet_address=wallet_address,
        name=payload.name,
        description=payload.description,
        retrieval_config=config,
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


@router.get("/{kb_id}", response_model=KBResponse)
def get_kb(kb_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> KnowledgeBase:
    return _get_kb_or_404(db, wallet_address, kb_id)


@router.patch("/{kb_id}", response_model=KBResponse)
def update_kb(
    kb_id: int,
    payload: KBUpdateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> KnowledgeBase:
    kb = _get_kb_or_404(db, wallet_address, kb_id)
    previous_config = _normalize_kb_config(kb.retrieval_config)
    updates = payload.model_dump(exclude_none=True, exclude_unset=True, exclude={"retrieval_config"})
    reindex_needed = False
    if payload.retrieval_config is not None:
        config_updates = payload.retrieval_config.model_dump(exclude_none=True, exclude_unset=True)
        next_config = {**previous_config, **config_updates}
        updates["retrieval_config"] = next_config
        reindex_needed = _chunking_config_changed(previous_config, next_config)
    for key, value in updates.items():
        setattr(kb, key, value)
    db.commit()
    db.refresh(kb)
    if reindex_needed:
        _schedule_reindex_if_needed(db, wallet_address, kb.id)
        db.refresh(kb)
    return kb


@router.delete("/{kb_id}")
def delete_kb(kb_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    kb = _get_kb_or_404(db, wallet_address, kb_id)
    _delete_kb_resources(db, wallet_address, kb.id)
    db.delete(kb)
    db.commit()
    return {"ok": True}


@router.get("/{kb_id}/stats", response_model=KBStatsResponse)
def kb_stats(kb_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> KBStatsResponse:
    _get_kb_or_404(db, wallet_address, kb_id)
    bindings_count = db.scalar(select(func.count(SourceBinding.id)).where(SourceBinding.kb_id == kb_id)) or 0
    documents_count = db.scalar(select(func.count(ImportedDocument.id)).where(ImportedDocument.kb_id == kb_id)) or 0
    chunks_count = db.scalar(select(func.count(ImportedChunk.id)).where(ImportedChunk.kb_id == kb_id)) or 0
    latest_task = db.scalar(
        select(ImportTask)
        .where(ImportTask.kb_id == kb_id)
        .where(ImportTask.owner_wallet_address == wallet_address)
        .order_by(ImportTask.created_at.desc())
    )
    return KBStatsResponse(
        kb_id=kb_id,
        bindings_count=int(bindings_count),
        documents_count=int(documents_count),
        chunks_count=int(chunks_count),
        latest_task_status=latest_task.status if latest_task else None,
        latest_task_finished_at=latest_task.finished_at if latest_task else None,
    )
