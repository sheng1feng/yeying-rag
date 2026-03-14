from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.models import ImportedDocument, ImportTask, KnowledgeBase, SourceBinding


ACTIVE_TASK_STATUSES = {"pending", "running", "cancel_requested"}
FAILED_TASK_STATUSES = {"failed", "partial_success"}


class BindingService:
    @staticmethod
    def normalize_path(path: str) -> str:
        normalized = "/" + str(path or "/").strip().lstrip("/")
        return normalized.rstrip("/") or "/"

    @classmethod
    def _path_contains(cls, base_path: str, target_path: str) -> bool:
        normalized_base = cls.normalize_path(base_path)
        normalized_target = cls.normalize_path(target_path)
        if normalized_base == "/":
            return True
        return normalized_target == normalized_base or normalized_target.startswith(f"{normalized_base}/")

    @classmethod
    def binding_covers_path(cls, binding: SourceBinding, source_path: str) -> bool:
        binding_path = cls.normalize_path(binding.source_path)
        target_path = cls.normalize_path(source_path)
        scope_type = str(binding.scope_type or "file").strip().lower()
        if scope_type == "directory":
            return cls._path_contains(binding_path, target_path)
        return target_path == binding_path

    @classmethod
    def paths_overlap(cls, left_path: str, right_path: str) -> bool:
        return cls._path_contains(left_path, right_path) or cls._path_contains(right_path, left_path)

    @classmethod
    def task_impacts_binding(cls, binding: SourceBinding, source_paths: list[str] | None) -> bool:
        paths = [path for path in (source_paths or []) if str(path or "").strip()]
        if not paths:
            return False
        return any(cls.paths_overlap(binding.source_path, path) for path in paths)

    def list_binding_summaries(self, db: Session, kb: KnowledgeBase) -> list[dict]:
        bindings = list(
            db.scalars(
                select(SourceBinding)
                .where(SourceBinding.kb_id == kb.id)
                .order_by(SourceBinding.created_at.asc(), SourceBinding.id.asc())
            ).all()
        )
        if not bindings:
            return []

        documents = list(
            db.scalars(
                select(ImportedDocument)
                .where(ImportedDocument.kb_id == kb.id)
                .where(ImportedDocument.owner_wallet_address == kb.owner_wallet_address)
                .order_by(ImportedDocument.updated_at.desc(), ImportedDocument.id.desc())
            ).all()
        )
        tasks = list(
            db.scalars(
                select(ImportTask)
                .where(ImportTask.kb_id == kb.id)
                .where(ImportTask.owner_wallet_address == kb.owner_wallet_address)
                .order_by(ImportTask.created_at.desc(), ImportTask.id.desc())
            ).all()
        )

        summaries: list[dict] = []
        for binding in bindings:
            matching_documents = [document for document in documents if self.binding_covers_path(binding, document.source_path)]
            matching_tasks = [task for task in tasks if self.task_impacts_binding(binding, task.source_paths)]
            latest_task = matching_tasks[0] if matching_tasks else None
            active_task_count = sum(1 for task in matching_tasks if task.status in ACTIVE_TASK_STATUSES)
            document_count = len(matching_documents)
            chunk_count = sum(int(document.chunk_count or 0) for document in matching_documents)
            last_document_indexed_at = max(
                (document.last_indexed_at for document in matching_documents if document.last_indexed_at is not None),
                default=None,
            )
            sync_status, status_reason = self._resolve_sync_status(
                binding=binding,
                document_count=document_count,
                active_task_count=active_task_count,
                latest_task=latest_task,
            )
            summaries.append(
                {
                    "id": binding.id,
                    "kb_id": binding.kb_id,
                    "source_type": binding.source_type,
                    "source_path": binding.source_path,
                    "scope_type": binding.scope_type,
                    "enabled": binding.enabled,
                    "last_imported_at": binding.last_imported_at,
                    "sync_status": sync_status,
                    "status_reason": status_reason,
                    "document_count": document_count,
                    "chunk_count": chunk_count,
                    "last_document_indexed_at": last_document_indexed_at,
                    "latest_task_id": latest_task.id if latest_task is not None else None,
                    "latest_task_status": latest_task.status if latest_task is not None else None,
                    "latest_task_finished_at": latest_task.finished_at if latest_task is not None else None,
                    "active_task_count": active_task_count,
                }
            )
        return summaries

    def build_workbench(self, db: Session, kb: KnowledgeBase) -> dict:
        binding_summaries = self.list_binding_summaries(db, kb)
        documents = list(
            db.scalars(
                select(ImportedDocument)
                .where(ImportedDocument.kb_id == kb.id)
                .where(ImportedDocument.owner_wallet_address == kb.owner_wallet_address)
            ).all()
        )
        tasks = list(
            db.scalars(
                select(ImportTask)
                .where(ImportTask.kb_id == kb.id)
                .where(ImportTask.owner_wallet_address == kb.owner_wallet_address)
                .order_by(ImportTask.created_at.desc(), ImportTask.id.desc())
                .limit(5)
            ).all()
        )
        counts = {
            "total": len(binding_summaries),
            "enabled": sum(1 for item in binding_summaries if item["enabled"]),
            "disabled": sum(1 for item in binding_summaries if not item["enabled"]),
            "indexed": sum(1 for item in binding_summaries if item["sync_status"] == "indexed"),
            "syncing": sum(1 for item in binding_summaries if item["sync_status"] == "syncing"),
            "failed": sum(1 for item in binding_summaries if item["sync_status"] == "failed"),
            "pending_sync": sum(1 for item in binding_summaries if item["sync_status"] == "pending_sync"),
        }
        documents_count = len(documents)
        chunks_count = sum(int(document.chunk_count or 0) for document in documents)
        latest_task = tasks[0] if tasks else None
        return {
            "kb_id": kb.id,
            "kb_name": kb.name,
            "kb_description": kb.description,
            "kb_status": kb.status,
            "stats": {
                "kb_id": kb.id,
                "bindings_count": counts["total"],
                "documents_count": documents_count,
                "chunks_count": chunks_count,
                "latest_task_status": latest_task.status if latest_task is not None else None,
                "latest_task_finished_at": latest_task.finished_at if latest_task is not None else None,
            },
            "binding_status_counts": counts,
            "bindings": binding_summaries,
            "recent_tasks": [
                {
                    "id": task.id,
                    "task_type": task.task_type,
                    "status": task.status,
                    "source_paths": list(task.source_paths or []),
                    "created_at": task.created_at,
                    "finished_at": task.finished_at,
                }
                for task in tasks
            ],
        }

    @staticmethod
    def _resolve_sync_status(
        binding: SourceBinding,
        document_count: int,
        active_task_count: int,
        latest_task: ImportTask | None,
    ) -> tuple[str, str]:
        if not binding.enabled:
            return "disabled", "binding disabled"
        if active_task_count > 0:
            return "syncing", "sync task in progress"
        if latest_task is not None and latest_task.status in FAILED_TASK_STATUSES:
            return "failed", f"latest task {latest_task.status}"
        if document_count > 0:
            return "indexed", "indexed documents available"
        return "pending_sync", "no indexed documents yet"
