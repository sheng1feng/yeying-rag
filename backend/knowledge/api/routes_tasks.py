from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.models import ImportTask, ImportTaskItem, KnowledgeBase
from knowledge.schemas.tasks import TaskCreateRequest, TaskResponse
from knowledge.services.ingestion import IngestionService
from knowledge.utils.time import utc_now
from knowledge.workers.runner import Worker


router = APIRouter(tags=["ingestion_tasks"])
ingestion_service = IngestionService()
worker = Worker()
TERMINAL_TASK_STATUSES = {"succeeded", "failed", "partial_success", "canceled"}


def _queue_context(db: Session) -> tuple[ImportTask | None, dict[int, int]]:
    running_task = db.scalar(
        select(ImportTask)
        .where(ImportTask.status.in_(("running", "cancel_requested")))
        .order_by(ImportTask.started_at.asc().nulls_last(), ImportTask.created_at.asc(), ImportTask.id.asc())
    )
    pending_ids = list(
        db.scalars(
            select(ImportTask.id)
            .where(ImportTask.status == "pending")
            .order_by(ImportTask.created_at.asc(), ImportTask.id.asc())
        ).all()
    )
    return running_task, {task_id: index + 1 for index, task_id in enumerate(pending_ids)}


def _serialize_task(task: ImportTask, running_task: ImportTask | None, pending_positions: dict[int, int]) -> dict:
    status = str(task.status or "")
    queue_state = None
    if status == "pending":
        queue_state = "queued"
    elif status == "running":
        queue_state = "running"
    elif status == "cancel_requested":
        queue_state = "cancelling"
    elif status == "canceled":
        queue_state = "canceled"
    return {
        "id": task.id,
        "owner_wallet_address": task.owner_wallet_address,
        "kb_id": task.kb_id,
        "task_type": task.task_type,
        "status": task.status,
        "source_paths": task.source_paths,
        "stats_json": task.stats_json,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "queue_state": queue_state,
        "queue_position": pending_positions.get(task.id),
        "current_running_task_id": running_task.id if running_task is not None else None,
        "current_running_task_type": running_task.task_type if running_task is not None else None,
        "cancelable": status not in TERMINAL_TASK_STATUSES,
    }


def _validate_kb(db: Session, wallet_address: str, kb_id: int) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    return kb


@router.post("/kbs/{kb_id}/tasks/import", response_model=TaskResponse)
def create_import_task(
    kb_id: int,
    payload: TaskCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ImportTask:
    _validate_kb(db, wallet_address, kb_id)
    task = ingestion_service.create_task(db, wallet_address, kb_id, "import", payload.source_paths)
    running_task, pending_positions = _queue_context(db)
    return _serialize_task(task, running_task, pending_positions)


@router.post("/kbs/{kb_id}/tasks/reindex", response_model=TaskResponse)
def create_reindex_task(
    kb_id: int,
    payload: TaskCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ImportTask:
    _validate_kb(db, wallet_address, kb_id)
    task = ingestion_service.create_task(db, wallet_address, kb_id, "reindex", payload.source_paths)
    running_task, pending_positions = _queue_context(db)
    return _serialize_task(task, running_task, pending_positions)


@router.post("/kbs/{kb_id}/tasks/delete", response_model=TaskResponse)
def create_delete_task(
    kb_id: int,
    payload: TaskCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> ImportTask:
    _validate_kb(db, wallet_address, kb_id)
    task = ingestion_service.create_task(db, wallet_address, kb_id, "delete", payload.source_paths)
    running_task, pending_positions = _queue_context(db)
    return _serialize_task(task, running_task, pending_positions)


@router.get("/tasks", response_model=list[TaskResponse])
def list_tasks(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> list[dict]:
    tasks = list(
        db.scalars(
            select(ImportTask)
            .where(ImportTask.owner_wallet_address == wallet_address)
            .order_by(ImportTask.created_at.desc(), ImportTask.id.desc())
        ).all()
    )
    running_task, pending_positions = _queue_context(db)
    return [_serialize_task(task, running_task, pending_positions) for task in tasks]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    task = db.get(ImportTask, task_id)
    if task is None or task.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="task not found")
    running_task, pending_positions = _queue_context(db)
    return _serialize_task(task, running_task, pending_positions)


@router.post("/tasks/process-pending")
def process_pending_tasks(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    _ = wallet_address
    processed = worker.process_once()
    pending = int(db.scalar(select(func.count(ImportTask.id)).where(ImportTask.status == "pending")) or 0)
    worker_busy = worker.is_run_locked() and processed == 0
    return {
        "ok": True,
        "processed": processed,
        "pending": pending,
        "worker_busy": worker_busy,
        "message": "已有 worker 正在处理，当前任务保持排队中。" if worker_busy and pending > 0 else "",
    }


@router.post("/tasks/{task_id}/retry", response_model=TaskResponse)
def retry_task(task_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> ImportTask:
    task = db.get(ImportTask, task_id)
    if task is None or task.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="task not found")
    cloned = ingestion_service.create_task(
        db=db,
        wallet_address=wallet_address,
        kb_id=task.kb_id,
        task_type=task.task_type,
        source_paths=list(task.source_paths),
    )
    running_task, pending_positions = _queue_context(db)
    return _serialize_task(cloned, running_task, pending_positions)


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    task = db.get(ImportTask, task_id)
    if task is None or task.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status in TERMINAL_TASK_STATUSES:
        raise HTTPException(status_code=400, detail="task already finished")
    if task.status == "pending":
        task.status = "canceled"
        task.finished_at = utc_now()
        task.error_message = "canceled by user"
        task.stats_json = {
            **(task.stats_json or {}),
            "canceled": True,
            "rollback": {"applied": False, "reason": "task not started"},
        }
    else:
        task.status = "cancel_requested"
        task.error_message = "cancel requested by user"
    db.commit()
    db.refresh(task)
    running_task, pending_positions = _queue_context(db)
    return _serialize_task(task, running_task, pending_positions)


@router.get("/tasks/{task_id}/items")
def task_items(task_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> list[dict]:
    task = db.get(ImportTask, task_id)
    if task is None or task.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="task not found")
    items = list(
        db.scalars(
            select(ImportTaskItem)
            .where(ImportTaskItem.task_id == task_id)
            .order_by(ImportTaskItem.created_at.asc())
        ).all()
    )
    return [
        {
            "id": item.id,
            "source_path": item.source_path,
            "file_name": item.file_name,
            "status": item.status,
            "message": item.message,
            "processed_chunks": item.processed_chunks,
            "source_version": item.source_version,
            "created_at": item.created_at,
        }
        for item in items
    ]
