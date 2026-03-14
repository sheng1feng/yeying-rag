from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.models import ImportTask
from knowledge.services.bindings import BindingService
from knowledge.utils.time import utc_now


ACTIVE_TASK_STATUSES = {"pending", "running", "cancel_requested"}
RUNNING_TASK_STATUSES = {"running", "cancel_requested"}


class TaskQueueService:
    def __init__(self) -> None:
        self.settings = get_settings()

    @staticmethod
    def normalize_path(path: str) -> str:
        return BindingService.normalize_path(path)

    @classmethod
    def compress_source_paths(cls, source_paths: list[str]) -> list[str]:
        normalized = [cls.normalize_path(path) for path in source_paths if str(path or "").strip()]
        unique_paths = list(dict.fromkeys(normalized))
        if not unique_paths:
            return []
        minimal_paths: set[str] = set(unique_paths)
        for left in unique_paths:
            for right in unique_paths:
                if left == right:
                    continue
                if cls.path_contains(left, right):
                    minimal_paths.discard(right)
        return [path for path in unique_paths if path in minimal_paths]

    @classmethod
    def path_contains(cls, base_path: str, target_path: str) -> bool:
        normalized_base = cls.normalize_path(base_path)
        normalized_target = cls.normalize_path(target_path)
        if normalized_base == "/":
            return True
        return normalized_target == normalized_base or normalized_target.startswith(f"{normalized_base}/")

    @classmethod
    def paths_overlap(cls, left_path: str, right_path: str) -> bool:
        return cls.path_contains(left_path, right_path) or cls.path_contains(right_path, left_path)

    @classmethod
    def task_paths_overlap(cls, source_paths: list[str], other_paths: list[str]) -> bool:
        normalized_left = cls.compress_source_paths(source_paths)
        normalized_right = cls.compress_source_paths(other_paths)
        return any(cls.paths_overlap(left, right) for left in normalized_left for right in normalized_right)

    def find_active_duplicate_or_conflict(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        task_type: str,
        source_paths: list[str],
    ) -> tuple[ImportTask | None, str | None]:
        normalized_paths = self.compress_source_paths(source_paths)
        if not normalized_paths:
            return None, None
        active_tasks = list(
            db.scalars(
                select(ImportTask)
                .where(ImportTask.owner_wallet_address == wallet_address)
                .where(ImportTask.kb_id == kb_id)
                .where(ImportTask.status.in_(tuple(ACTIVE_TASK_STATUSES)))
                .order_by(ImportTask.created_at.asc(), ImportTask.id.asc())
            ).all()
        )
        for task in active_tasks:
            task_paths = self.compress_source_paths(list(task.source_paths or []))
            if task.task_type == task_type and task_paths == normalized_paths:
                return task, None
            if task.task_type != task_type and "delete" in {task.task_type, task_type} and self.task_paths_overlap(normalized_paths, task_paths):
                return None, f"overlapping active {task.task_type} task #{task.id} already exists"
        return None, None

    def stale_before(self):
        return utc_now() - timedelta(seconds=max(15, int(self.settings.worker_run_lease_ttl_seconds)))

    def reclaim_stale_tasks(self, db: Session) -> int:
        stale_before = self.stale_before()
        candidates = list(
            db.scalars(
                select(ImportTask)
                .where(ImportTask.status.in_(tuple(RUNNING_TASK_STATUSES)))
                .where(
                    (ImportTask.heartbeat_at.is_(None) & (ImportTask.started_at < stale_before))
                    | (ImportTask.heartbeat_at < stale_before)
                )
                .order_by(ImportTask.started_at.asc().nulls_last(), ImportTask.id.asc())
            ).all()
        )
        reclaimed = 0
        for task in candidates:
            if task.status == "cancel_requested":
                task.status = "canceled"
                task.finished_at = utc_now()
                task.error_message = "worker heartbeat stale after cancel request"
            else:
                task.status = "pending"
                task.error_message = "worker heartbeat stale; task reclaimed"
            task.claimed_by = None
            task.claimed_at = None
            task.heartbeat_at = None
            task.last_stage = "reclaimed"
            reclaimed += 1
        if reclaimed:
            db.commit()
        return reclaimed

    def claim_next_task(self, db: Session, worker_name: str) -> ImportTask | None:
        self.reclaim_stale_tasks(db)
        now = utc_now()
        stale_before = self.stale_before()
        active_counts = dict(
            db.execute(
                select(ImportTask.owner_wallet_address, func.count(ImportTask.id))
                .where(ImportTask.status.in_(tuple(RUNNING_TASK_STATUSES)))
                .where(
                    (ImportTask.heartbeat_at.is_not(None) & (ImportTask.heartbeat_at >= stale_before))
                    | (ImportTask.heartbeat_at.is_(None) & (ImportTask.started_at.is_not(None)) & (ImportTask.started_at >= stale_before))
                )
                .group_by(ImportTask.owner_wallet_address)
            ).all()
        )
        candidates = list(
            db.scalars(
                select(ImportTask)
                .where(ImportTask.status == "pending")
                .order_by(ImportTask.created_at.asc(), ImportTask.id.asc())
            ).all()
        )
        for candidate in candidates:
            owner_count = int(active_counts.get(candidate.owner_wallet_address, 0))
            if owner_count >= max(1, int(self.settings.worker_max_active_tasks_per_user)):
                continue
            owner_active_count = (
                select(func.count(ImportTask.id))
                .where(ImportTask.owner_wallet_address == candidate.owner_wallet_address)
                .where(ImportTask.status.in_(tuple(RUNNING_TASK_STATUSES)))
                .where(
                    (ImportTask.heartbeat_at.is_not(None) & (ImportTask.heartbeat_at >= stale_before))
                    | (ImportTask.heartbeat_at.is_(None) & (ImportTask.started_at.is_not(None)) & (ImportTask.started_at >= stale_before))
                )
            )
            result = db.execute(
                update(ImportTask)
                .where(ImportTask.id == candidate.id)
                .where(ImportTask.status == "pending")
                .where(owner_active_count.scalar_subquery() < max(1, int(self.settings.worker_max_active_tasks_per_user)))
                .values(
                    status="running",
                    claimed_by=worker_name,
                    claimed_at=now,
                    heartbeat_at=now,
                    started_at=func.coalesce(ImportTask.started_at, now),
                    attempt=func.coalesce(ImportTask.attempt, 0) + 1,
                    last_stage="claimed",
                    error_message="",
                )
            )
            if (result.rowcount or 0) != 1:
                db.rollback()
                continue
            db.commit()
            claimed = db.get(ImportTask, candidate.id)
            if claimed is None:
                continue
            active_counts[claimed.owner_wallet_address] = owner_count + 1
            return claimed
        return None

    @staticmethod
    def touch_task_heartbeat(db: Session, task_id: int, worker_name: str, stage: str | None = None) -> bool:
        values: dict[str, object] = {"heartbeat_at": utc_now()}
        if stage is not None:
            values["last_stage"] = stage
        result = db.execute(
            update(ImportTask)
            .where(ImportTask.id == task_id)
            .where(ImportTask.claimed_by == worker_name)
            .where(ImportTask.status.in_(tuple(RUNNING_TASK_STATUSES)))
            .values(**values)
        )
        if (result.rowcount or 0) == 1:
            db.commit()
            return True
        db.rollback()
        return False

    @staticmethod
    def active_tasks_by_worker(db: Session, stale_before) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        rows = db.execute(
            select(ImportTask.claimed_by, func.count(ImportTask.id))
            .where(ImportTask.claimed_by.is_not(None))
            .where(ImportTask.status.in_(tuple(RUNNING_TASK_STATUSES)))
            .where(
                (ImportTask.heartbeat_at.is_not(None) & (ImportTask.heartbeat_at >= stale_before))
                | (ImportTask.heartbeat_at.is_(None) & (ImportTask.started_at.is_not(None)) & (ImportTask.started_at >= stale_before))
            )
            .group_by(ImportTask.claimed_by)
        ).all()
        for worker_name, count in rows:
            if worker_name:
                counts[str(worker_name)] = int(count or 0)
        return counts

    def has_active_claims(self, db: Session) -> bool:
        stale_before = self.stale_before()
        return bool(
            db.scalar(
                select(func.count(ImportTask.id))
                .where(ImportTask.status.in_(tuple(RUNNING_TASK_STATUSES)))
                .where(
                    (ImportTask.heartbeat_at.is_not(None) & (ImportTask.heartbeat_at >= stale_before))
                    | (ImportTask.heartbeat_at.is_(None) & (ImportTask.started_at.is_not(None)) & (ImportTask.started_at >= stale_before))
                )
            )
            or 0
        )
