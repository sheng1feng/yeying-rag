from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import unquote
import fcntl

from sqlalchemy import select
from sqlalchemy.engine import make_url

from knowledge.core.settings import get_settings
from knowledge.db.session import session_scope
from knowledge.models import ImportTask, WorkerStatus
from knowledge.services.ingestion import IngestionService
from knowledge.services.memory import MemoryService
from knowledge.utils.time import utc_now


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.ingestion_service = IngestionService()
        self.memory_service = MemoryService()
        self.lock_file_path = self._resolve_lock_file_path()

    def process_once(self) -> int:
        lock_handle = self._acquire_run_lock()
        if lock_handle is None:
            return 0
        processed = 0
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
                    self.ingestion_service.process_task(db, task)
                    processed += 1
                self._heartbeat(db, status="idle", processed_count=processed, last_error="")
            return processed
        finally:
            self._release_run_lock(lock_handle)

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

    def _resolve_lock_file_path(self) -> Path:
        database = make_url(self.settings.database_url).database or ""
        if database and database != ":memory:":
            db_path = Path(unquote(database))
            if not db_path.is_absolute():
                db_path = (Path.cwd() / db_path).resolve()
            return db_path.with_name(f"{db_path.name}.worker.lock")
        return Path.cwd() / ".knowledge-worker.lock"

    def _acquire_run_lock(self):
        self.lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_file_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        return handle

    @staticmethod
    def _release_run_lock(handle) -> None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def is_run_locked(self) -> bool:
        self.lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_file_path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
        finally:
            handle.close()


if __name__ == "__main__":
    Worker().run_forever()
