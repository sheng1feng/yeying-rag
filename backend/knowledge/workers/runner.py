from __future__ import annotations

from datetime import timedelta
import time

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from knowledge.core.settings import get_settings
from knowledge.db.session import session_scope
from knowledge.models import ImportTask, WorkerStatus
from knowledge.services.ingestion import IngestionService
from knowledge.services.memory import MemoryService
from knowledge.utils.time import utc_now


class Worker:
    RUN_LEASE_NAME = "__knowledge-task-runner__"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.ingestion_service = IngestionService()
        self.memory_service = MemoryService()

    def process_once(self) -> int:
        processed = 0
        if not self._acquire_run_lease():
            return 0
        try:
            with session_scope() as db:
                self._heartbeat(db, status="running", last_error="")
                self.memory_service.cleanup_expired_short_term(db)
                tasks = list(
                    db.scalars(
                        select(ImportTask)
                        .where(ImportTask.status == "pending")
                        .order_by(ImportTask.created_at.asc(), ImportTask.id.asc())
                    ).all()
                )
                for task in tasks:
                    self._touch_run_lease(db)
                    self.ingestion_service.process_task(db, task)
                    processed += 1
                self._heartbeat(db, status="idle", processed_count=processed, last_error="")
            return processed
        finally:
            self._release_run_lease()

    def run_forever(self) -> None:
        while True:
            try:
                processed = self.process_once()
                if processed == 0:
                    time.sleep(self.settings.worker_poll_interval_seconds)
            except Exception as exc:  # noqa: BLE001
                with session_scope() as db:
                    self._heartbeat(db, status="error", last_error=str(exc))
                time.sleep(self.settings.worker_poll_interval_seconds)

    def _heartbeat(self, db, status: str, processed_count: int = 0, last_error: str = "") -> None:
        record = db.get(WorkerStatus, self.settings.worker_name)
        if record is None:
            record = WorkerStatus(worker_name=self.settings.worker_name)
            db.add(record)
        record.status = status
        record.last_seen_at = utc_now()
        if processed_count:
            record.processed_count += processed_count
            record.last_processed_at = utc_now()
        if last_error:
            record.last_error = last_error
        db.flush()

    def _lease_stale_before(self):
        return utc_now() - timedelta(seconds=max(15, int(self.settings.worker_run_lease_ttl_seconds)))

    def _acquire_run_lease(self) -> bool:
        now = utc_now()
        stale_before = self._lease_stale_before()
        with session_scope() as db:
            result = db.execute(
                update(WorkerStatus)
                .where(WorkerStatus.worker_name == self.RUN_LEASE_NAME)
                .where(or_(WorkerStatus.status != "running", WorkerStatus.last_seen_at < stale_before))
                .values(status="running", last_seen_at=now, last_error="")
            )
            if (result.rowcount or 0) == 1:
                return True
            lease = db.get(WorkerStatus, self.RUN_LEASE_NAME)
            if lease is not None and lease.status == "running" and lease.last_seen_at >= stale_before:
                return False
            if lease is None:
                try:
                    db.add(WorkerStatus(worker_name=self.RUN_LEASE_NAME, status="running", last_seen_at=now))
                    db.flush()
                    return True
                except IntegrityError:
                    db.rollback()
                    return False
            lease.status = "running"
            lease.last_seen_at = now
            lease.last_error = ""
            db.flush()
            return True

    def _touch_run_lease(self, db) -> None:
        lease = db.get(WorkerStatus, self.RUN_LEASE_NAME)
        if lease is None:
            lease = WorkerStatus(worker_name=self.RUN_LEASE_NAME, status="running", last_seen_at=utc_now())
            db.add(lease)
            db.flush()
            return
        lease.status = "running"
        lease.last_seen_at = utc_now()
        db.flush()

    def _release_run_lease(self) -> None:
        with session_scope() as db:
            lease = db.get(WorkerStatus, self.RUN_LEASE_NAME)
            if lease is None:
                return
            lease.status = "idle"
            lease.last_seen_at = utc_now()
            db.flush()

    def is_run_locked(self) -> bool:
        with session_scope() as db:
            lease = db.get(WorkerStatus, self.RUN_LEASE_NAME)
            if lease is None:
                return False
            return lease.status == "running" and lease.last_seen_at >= self._lease_stale_before()


if __name__ == "__main__":
    Worker().run_forever()
