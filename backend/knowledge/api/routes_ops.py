from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.models import ImportedChunk, ImportedDocument, ImportTask, KnowledgeBase, LongTermMemory, MemoryIngestionEvent, ShortTermMemory, UploadRecord, WorkerStatus
from knowledge.core.settings import get_settings
from knowledge.services.vector_store import build_vector_store
from knowledge.utils.time import utc_now


router = APIRouter(prefix="/ops", tags=["ops"], dependencies=[Depends(get_current_wallet)])


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    return {
        "knowledge_bases": int(db.scalar(select(func.count(KnowledgeBase.id))) or 0),
        "documents": int(db.scalar(select(func.count(ImportedDocument.id))) or 0),
        "chunks": int(db.scalar(select(func.count(ImportedChunk.id))) or 0),
        "tasks_total": int(db.scalar(select(func.count(ImportTask.id))) or 0),
        "tasks_pending": int(db.scalar(select(func.count(ImportTask.id)).where(ImportTask.status == "pending")) or 0),
        "long_term_memories": int(db.scalar(select(func.count(LongTermMemory.id))) or 0),
        "short_term_memories": int(db.scalar(select(func.count(ShortTermMemory.id))) or 0),
        "memory_ingestions": int(db.scalar(select(func.count(MemoryIngestionEvent.id))) or 0),
        "uploads": int(db.scalar(select(func.count(UploadRecord.id))) or 0),
    }


@router.get("/stores/health")
def stores_health(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    try:
        db.execute(select(1))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"
    vector_backend = build_vector_store()
    try:
        vector_status = vector_backend.health()
    except Exception as exc:  # noqa: BLE001
        vector_status = {"backend": settings.vector_store_mode, "status": f"error: {exc}"}
    model_status = "configured" if settings.model_provider_mode != "mock" and settings.model_gateway_base_url else "mock-or-not-configured"
    return {
        "database": db_status,
        "vector_store_mode": settings.vector_store_mode,
        "vector_store_status": vector_status,
        "model_provider_mode": settings.model_provider_mode,
        "model_provider_status": model_status,
        "warehouse_gateway_mode": settings.warehouse_gateway_mode,
        "warehouse_base_url": settings.warehouse_base_url,
    }


@router.get("/workers")
def workers(db: Session = Depends(get_db)) -> list[dict]:
    rows = list(db.scalars(select(WorkerStatus).order_by(WorkerStatus.worker_name.asc())).all())
    now = utc_now()
    results = []
    for row in rows:
        stale = row.last_seen_at < now - timedelta(seconds=90)
        results.append(
            {
                "worker_name": row.worker_name,
                "status": "stale" if stale and row.status != "error" else row.status,
                "last_seen_at": row.last_seen_at,
                "last_processed_at": row.last_processed_at,
                "processed_count": row.processed_count,
                "last_error": row.last_error,
            }
        )
    return results


@router.get("/tasks/failures")
def recent_task_failures(trace_id: str | None = None, limit: int = 10, db: Session = Depends(get_db)) -> list[dict]:
    query_limit = max(limit, 100) if trace_id else limit
    rows = list(
        db.scalars(
            select(ImportTask)
            .where(ImportTask.status.in_(["failed", "partial_success"]))
            .order_by(ImportTask.finished_at.desc(), ImportTask.created_at.desc())
            .limit(query_limit)
        ).all()
    )
    if trace_id:
        rows = [
            row
            for row in rows
            if (row.stats_json or {}).get("trace_id") == trace_id
            or trace_id in ((row.stats_json or {}).get("related_trace_ids") or [])
        ]
        rows = rows[:limit]
    return [
        {
            "id": row.id,
            "kb_id": row.kb_id,
            "task_type": row.task_type,
            "status": row.status,
            "trace_id": (row.stats_json or {}).get("trace_id", ""),
            "source_paths": row.source_paths,
            "error_message": row.error_message,
            "stats_json": row.stats_json,
            "finished_at": row.finished_at,
            "created_at": row.created_at,
        }
        for row in rows
    ]
