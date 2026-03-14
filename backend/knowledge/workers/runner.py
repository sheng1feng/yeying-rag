from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
import time
from threading import Event, Thread

from knowledge.core.settings import get_settings
from knowledge.db.session import session_scope
from knowledge.models import ImportTask, WorkerStatus
from knowledge.services.ingestion import IngestionService
from knowledge.services.memory import MemoryService
from knowledge.services.task_queue import TaskQueueService
from knowledge.utils.time import utc_now


class TaskHeartbeat:
    def __init__(self, worker_name: str, task_id: int, interval_seconds: int) -> None:
        self.worker_name = worker_name
        self.task_id = task_id
        self.interval_seconds = max(5, int(interval_seconds))
        self.task_queue = TaskQueueService()
        self._stop = Event()
        self._thread = Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval_seconds)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            with session_scope() as db:
                alive = self.task_queue.touch_task_heartbeat(db, self.task_id, self.worker_name)
            if not alive:
                return


class Worker:
    RUN_LEASE_NAME = "__knowledge-task-runner__"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.memory_service = MemoryService()
        self.task_queue = TaskQueueService()
        self._last_housekeeping_at = None

    def process_once(self) -> int:
        processed = 0
        try:
            self._run_housekeeping_if_due()
            self._heartbeat(status="running", last_error="")
            max_workers = self._task_concurrency()
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                while True:
                    claimed_task_ids = self._claim_batch(max_workers)
                    if not claimed_task_ids:
                        break
                    futures = [executor.submit(self._process_claimed_task, task_id) for task_id in claimed_task_ids]
                    for future in as_completed(futures):
                        future.result()
                        processed += 1
                        self._heartbeat(status="running", last_error="")
            self._heartbeat(status="idle", processed_count=processed, last_error="")
            return processed
        except Exception as exc:  # noqa: BLE001
            self._heartbeat(status="error", last_error=str(exc))
            raise

    def run_forever(self) -> None:
        while True:
            try:
                processed = self.process_once()
                if processed == 0:
                    time.sleep(self.settings.worker_poll_interval_seconds)
            except Exception:
                time.sleep(self.settings.worker_poll_interval_seconds)

    def is_run_locked(self) -> bool:
        with session_scope() as db:
            return self.task_queue.has_active_claims(db)

    def _claim_batch(self, limit: int) -> list[int]:
        claimed: list[int] = []
        with session_scope() as db:
            for _ in range(limit):
                task = self.task_queue.claim_next_task(db, self.settings.worker_name)
                if task is None:
                    break
                claimed.append(task.id)
        return claimed

    def _process_claimed_task(self, task_id: int) -> None:
        heartbeat = TaskHeartbeat(
            worker_name=self.settings.worker_name,
            task_id=task_id,
            interval_seconds=self.settings.worker_task_heartbeat_interval_seconds,
        )
        heartbeat.start()
        try:
            ingestion_service = IngestionService()
            with session_scope() as db:
                task = db.get(ImportTask, task_id)
                if task is None:
                    return
                ingestion_service.process_task(db, task)
        except Exception as exc:  # noqa: BLE001
            with session_scope() as db:
                task = db.get(ImportTask, task_id)
                if task is not None and task.status not in {"succeeded", "failed", "partial_success", "canceled"}:
                    task.status = "failed"
                    task.finished_at = utc_now()
                    task.error_message = str(exc)
                    task.last_stage = "worker_error"
                db.commit()
            raise
        finally:
            heartbeat.stop()

    def _heartbeat(self, status: str, processed_count: int = 0, last_error: str = "") -> None:
        with session_scope() as db:
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

    def _run_housekeeping_if_due(self) -> None:
        now = utc_now()
        if self._last_housekeeping_at is not None and now - self._last_housekeeping_at < timedelta(minutes=5):
            return
        with session_scope() as db:
            self.memory_service.cleanup_expired_short_term(db)
        self._last_housekeeping_at = now

    def _task_concurrency(self) -> int:
        if self.settings.database_url.startswith("sqlite"):
            return 1
        return max(1, int(self.settings.worker_task_concurrency))


if __name__ == "__main__":
    Worker().run_forever()
