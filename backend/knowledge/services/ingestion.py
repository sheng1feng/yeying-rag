from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.models import EmbeddingRecord, ImportedChunk, ImportedDocument, ImportTask, ImportTaskItem, KnowledgeBase, SourceBinding
from knowledge.services.chunking import DocumentChunker
from knowledge.services.embedding import EmbeddingProvider, build_embedding_provider
from knowledge.services.filetypes import infer_file_type
from knowledge.services.parser import DocumentParser
from knowledge.services.vector_store import build_vector_store
from knowledge.services.warehouse import WarehouseGateway, WarehouseFileEntry, build_warehouse_gateway
from knowledge.services.warehouse_session import WarehouseSessionService
from knowledge.utils.time import utc_now
import uuid


class TaskCanceledError(Exception):
    def __init__(self, rollback_summary: dict | None = None) -> None:
        super().__init__("task canceled by user")
        self.rollback_summary = rollback_summary or {}


class IngestionService:
    def __init__(
        self,
        warehouse_gateway: WarehouseGateway | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.settings = get_settings()
        self.warehouse_gateway = warehouse_gateway or build_warehouse_gateway()
        self.embedding_provider = embedding_provider or build_embedding_provider()
        self.parser = DocumentParser()
        self.chunker = DocumentChunker()
        self.vector_store = build_vector_store()
        self.warehouse_session_service = WarehouseSessionService()

    def create_task(self, db: Session, wallet_address: str, kb_id: int, task_type: str, source_paths: list[str]) -> ImportTask:
        task = ImportTask(
            owner_wallet_address=wallet_address,
            kb_id=kb_id,
            task_type=task_type,
            source_paths=source_paths,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def process_task(self, db: Session, task: ImportTask) -> ImportTask:
        db.refresh(task)
        if task.status in {"cancel_requested", "canceled"}:
            task.status = "canceled"
            task.error_message = "canceled by user"
            task.started_at = task.started_at or utc_now()
            task.finished_at = utc_now()
            task.stats_json = {
                **(task.stats_json or {}),
                "canceled": True,
                "rollback": {"applied": False, "reason": "task canceled before execution"},
            }
            db.commit()
            return task
        task.status = "running"
        task.started_at = utc_now()
        task.error_message = ""
        db.commit()

        processed_files = 0
        processed_chunks = 0
        failed_files = 0
        skipped_files = 0
        deleted_files = 0
        rollback_plan: dict[str, dict] = {}
        try:
            self._raise_if_cancel_requested(db, task, rollback_plan)
            if task.task_type == "delete":
                deleted_files = self._handle_delete(db, task, rollback_plan)
            else:
                for source_path in task.source_paths:
                    self._raise_if_cancel_requested(db, task, rollback_plan)
                    try:
                        entries = self._iter_files(db, task.owner_wallet_address, source_path)
                    except Exception as exc:  # noqa: BLE001
                        failed_files += 1
                        self._record_task_item(
                            db,
                            task_id=task.id,
                            source_path=source_path,
                            file_name="",
                            status="failed",
                            message=str(exc),
                            processed_chunks=0,
                            source_version="",
                        )
                        task.error_message = f"{task.error_message}\n{source_path}: {exc}".strip()
                        continue
                    for file_entry in entries:
                        self._raise_if_cancel_requested(db, task, rollback_plan)
                        processed_files += 1
                        try:
                            chunks_created, item_status = self._index_file(db, task, file_entry, rollback_plan)
                            processed_chunks += chunks_created
                            if item_status == "skipped":
                                skipped_files += 1
                        except Exception as exc:  # noqa: BLE001
                            failed_files += 1
                            self._record_task_item(
                                db,
                                task_id=task.id,
                                source_path=file_entry.path,
                                file_name=file_entry.name,
                                status="failed",
                                message=str(exc),
                                processed_chunks=0,
                                source_version=file_entry.modified_at.isoformat() if file_entry.modified_at else "",
                            )
                            task.error_message = f"{task.error_message}\n{file_entry.path}: {exc}".strip()

            self._raise_if_cancel_requested(db, task, rollback_plan)
            task.status = "partial_success" if failed_files else "succeeded"
            task.stats_json = {
                "processed_files": processed_files,
                "processed_chunks": processed_chunks,
                "failed_files": failed_files,
                "skipped_files": skipped_files,
                "deleted_files": deleted_files,
            }
        except TaskCanceledError as exc:
            task.status = "canceled"
            task.error_message = "canceled by user"
            task.stats_json = {
                "processed_files": processed_files,
                "processed_chunks": processed_chunks,
                "failed_files": failed_files,
                "skipped_files": skipped_files,
                "deleted_files": deleted_files,
                "canceled": True,
                "rollback": exc.rollback_summary,
            }
        except Exception as exc:  # noqa: BLE001
            task.status = "failed"
            task.error_message = str(exc)
            task.stats_json = {
                "processed_files": processed_files,
                "processed_chunks": processed_chunks,
                "failed_files": failed_files,
                "skipped_files": skipped_files,
                "deleted_files": deleted_files,
            }
        task.finished_at = utc_now()
        db.commit()
        db.refresh(task)
        return task

    def delete_document_index(self, db: Session, document: ImportedDocument) -> None:
        self._delete_document_state(db, document)
        db.delete(document)

    def _iter_files(self, db: Session, wallet_address: str, source_path: str) -> list[WarehouseFileEntry]:
        access_token = self._get_access_token_for_path_if_needed(db, wallet_address, source_path)
        entries = self.warehouse_gateway.browse(wallet_address, source_path, access_token=access_token)
        if len(entries) == 1 and entries[0].path == source_path and entries[0].entry_type == "file":
            return entries
        files: list[WarehouseFileEntry] = []
        for entry in entries:
            if entry.entry_type == "file":
                files.append(entry)
                continue
            if entry.entry_type == "directory":
                files.extend(self._iter_files(db, wallet_address, entry.path))
        return files

    @staticmethod
    def _normalize_source_path(path: str) -> str:
        normalized = "/" + str(path or "/").strip().lstrip("/")
        return normalized.rstrip("/") or "/"

    def _find_related_binding(self, db: Session, kb_id: int, source_path: str) -> SourceBinding | None:
        normalized_source_path = self._normalize_source_path(source_path)
        bindings = list(
            db.scalars(
                select(SourceBinding)
                .where(SourceBinding.kb_id == kb_id)
                .where(SourceBinding.enabled.is_(True))
            ).all()
        )
        best_binding: SourceBinding | None = None
        best_priority: tuple[int, int] | None = None
        for binding in bindings:
            binding_path = self._normalize_source_path(binding.source_path)
            scope_type = str(binding.scope_type or "file").strip().lower()
            priority: tuple[int, int] | None = None
            if scope_type == "directory":
                if normalized_source_path == binding_path:
                    priority = (2, len(binding_path))
                elif binding_path != "/" and normalized_source_path.startswith(f"{binding_path}/"):
                    priority = (1, len(binding_path))
                elif binding_path == "/":
                    priority = (1, 1)
            elif normalized_source_path == binding_path:
                priority = (3, len(binding_path))
            if priority is None:
                continue
            if best_priority is None or priority > best_priority:
                best_binding = binding
                best_priority = priority
        return best_binding

    def _list_documents_for_delete(self, db: Session, task: ImportTask) -> list[ImportedDocument]:
        normalized_source_paths = [self._normalize_source_path(path) for path in task.source_paths if str(path or "").strip()]
        if not normalized_source_paths:
            return []
        documents = list(
            db.scalars(
                select(ImportedDocument)
                .where(ImportedDocument.kb_id == task.kb_id)
                .where(ImportedDocument.owner_wallet_address == task.owner_wallet_address)
                .order_by(ImportedDocument.source_path.asc(), ImportedDocument.id.asc())
            ).all()
        )
        matched_documents: list[ImportedDocument] = []
        matched_ids: set[int] = set()
        for document in documents:
            document_path = self._normalize_source_path(document.source_path)
            for source_path in normalized_source_paths:
                if source_path == "/":
                    matched = True
                else:
                    matched = document_path == source_path or document_path.startswith(f"{source_path}/")
                if not matched:
                    continue
                if document.id not in matched_ids:
                    matched_documents.append(document)
                    matched_ids.add(document.id)
                break
        return matched_documents

    def _index_file(self, db: Session, task: ImportTask, file_entry: WarehouseFileEntry, rollback_plan: dict[str, dict]) -> tuple[int, str]:
        kb = db.get(KnowledgeBase, task.kb_id)
        if kb is None:
            raise ValueError("knowledge base not found")

        source_kind = "app" if file_entry.path.startswith("/apps/") else "personal"
        access_token = self._get_access_token_for_path_if_needed(db, task.owner_wallet_address, file_entry.path)
        content = self.warehouse_gateway.read_file(task.owner_wallet_address, file_entry.path, access_token=access_token)
        parsed_text = self.parser.parse(file_entry.name, content)
        if not parsed_text.strip():
            raise ValueError("parsed text is empty")
        self._raise_if_cancel_requested(db, task, rollback_plan)

        config = {**self._default_config(), **(kb.retrieval_config or {})}
        file_type = infer_file_type(file_entry.name)
        chunks = self.chunker.chunk(file_entry.name, parsed_text, config)
        if not chunks:
            raise ValueError("no chunks created")
        self._raise_if_cancel_requested(db, task, rollback_plan)

        document = db.scalar(
            select(ImportedDocument)
            .where(ImportedDocument.kb_id == kb.id)
            .where(ImportedDocument.source_path == file_entry.path)
        )
        self._capture_document_snapshot(db, kb.id, task.owner_wallet_address, file_entry.path, rollback_plan)
        current_version = file_entry.modified_at.isoformat() if file_entry.modified_at else ""
        if (
            task.task_type == "import"
            and document is not None
            and document.source_etag_or_mtime
            and document.source_etag_or_mtime == current_version
        ):
            self._record_task_item(
                db,
                task_id=task.id,
                source_path=file_entry.path,
                file_name=file_entry.name,
                status="skipped",
                message="source unchanged",
                processed_chunks=0,
                source_version=current_version,
            )
            return 0, "skipped"
        if document is None:
            document = ImportedDocument(
                kb_id=kb.id,
                owner_wallet_address=task.owner_wallet_address,
                source_path=file_entry.path,
                source_file_name=file_entry.name,
                source_kind=source_kind,
                source_etag_or_mtime=current_version,
                parse_status="parsed",
            )
            db.add(document)
            db.flush()
        else:
            old_vector_ids = [row[0] for row in db.execute(select(EmbeddingRecord.vector_id).where(EmbeddingRecord.chunk_id.in_(select(ImportedChunk.id).where(ImportedChunk.document_id == document.id)))).all()]
            self.vector_store.delete_vectors([vector_id for vector_id in old_vector_ids if vector_id])
            db.execute(delete(EmbeddingRecord).where(EmbeddingRecord.chunk_id.in_(select(ImportedChunk.id).where(ImportedChunk.document_id == document.id))))
            db.execute(delete(ImportedChunk).where(ImportedChunk.document_id == document.id))
            document.source_etag_or_mtime = current_version
            document.parse_status = "parsed"

        use_db_vectors = self.settings.vector_store_mode != "weaviate"
        embeddings = self.embedding_provider.embed_texts([chunk.text for chunk in chunks]) if use_db_vectors else []
        self._raise_if_cancel_requested(db, task, rollback_plan)
        vector_payloads: list[dict] = []
        created = 0
        for index, chunk_data in enumerate(chunks):
            self._raise_if_cancel_requested(db, task, rollback_plan, rollback_current_transaction=True)
            chunk = ImportedChunk(
                document_id=document.id,
                kb_id=kb.id,
                owner_wallet_address=task.owner_wallet_address,
                chunk_index=index,
                text=chunk_data.text,
                metadata_json={
                    "source_path": file_entry.path,
                    "source_kind": source_kind,
                    "file_name": file_entry.name,
                    "file_type": file_type,
                    **chunk_data.metadata,
                },
            )
            db.add(chunk)
            db.flush()
            vector_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"knowledge:{kb.id}:{file_entry.path}:{index}"))
            embedding = EmbeddingRecord(
                chunk_id=chunk.id,
                kb_id=kb.id,
                owner_wallet_address=task.owner_wallet_address,
                vector_id=vector_id,
                embedding_model=str(config["embedding_model"]),
                vector_json=embeddings[index] if use_db_vectors else [],
            )
            db.add(embedding)
            vector_payloads.append(
                {
                    "vector_id": vector_id,
                    "text": chunk_data.text,
                    "metadata": {
                        "wallet_address": task.owner_wallet_address,
                        "kb_id": kb.id,
                        "document_id": document.id,
                        "chunk_id": chunk.id,
                        "source_path": file_entry.path,
                        "source_kind": source_kind,
                        "file_name": file_entry.name,
                        "file_type": file_type,
                        "chunk_index": index,
                        "source_version": current_version,
                        "chunk_strategy": chunk_data.metadata.get("chunk_strategy"),
                    },
                }
            )
            created += 1

        if self.settings.vector_store_mode == "weaviate":
            self._raise_if_cancel_requested(db, task, rollback_plan, rollback_current_transaction=True)
            self.vector_store.index_chunks(vector_payloads)

        document.chunk_count = created
        document.last_indexed_at = utc_now()
        binding = self._find_related_binding(db, kb.id, file_entry.path)
        if binding is not None:
            binding.last_imported_at = utc_now()
        self._record_task_item(
            db,
            task_id=task.id,
            source_path=file_entry.path,
            file_name=file_entry.name,
            status="indexed",
            message="indexed successfully",
            processed_chunks=created,
            source_version=current_version,
        )
        db.commit()
        return created, "indexed"

    def _handle_delete(self, db: Session, task: ImportTask, rollback_plan: dict[str, dict]) -> int:
        documents = self._list_documents_for_delete(db, task)
        deleted = 0
        for document in documents:
            self._capture_document_snapshot(db, task.kb_id, task.owner_wallet_address, document.source_path, rollback_plan)
            self.delete_document_index(db, document)
            self._record_task_item(
                db,
                task_id=task.id,
                source_path=document.source_path,
                file_name=document.source_file_name,
                status="deleted",
                message="document index deleted",
                processed_chunks=0,
                source_version=document.source_etag_or_mtime,
            )
            deleted += 1
        db.commit()
        return deleted

    def _default_config(self) -> dict:
        return {
            "chunk_size": self.settings.chunk_size,
            "chunk_overlap": self.settings.chunk_overlap,
            "retrieval_top_k": self.settings.retrieval_top_k,
            "memory_top_k": self.settings.memory_top_k,
            "embedding_model": self.settings.embedding_model,
        }

    def _get_access_token_if_needed(self, db: Session, wallet_address: str) -> str | None:
        return self._get_access_token_for_path_if_needed(db, wallet_address, "/personal")

    def _get_access_token_for_path_if_needed(self, db: Session, wallet_address: str, path: str) -> str | None:
        if self.settings.warehouse_gateway_mode == "bound_token":
            return self.warehouse_session_service.get_access_token_for_path(db, wallet_address, path)
        return None

    def _record_task_item(
        self,
        db: Session,
        task_id: int,
        source_path: str,
        file_name: str,
        status: str,
        message: str,
        processed_chunks: int,
        source_version: str,
    ) -> None:
        item = ImportTaskItem(
            task_id=task_id,
            source_path=source_path,
            file_name=file_name,
            status=status,
            message=message,
            processed_chunks=processed_chunks,
            source_version=source_version,
        )
        db.add(item)

    def _capture_document_snapshot(
        self,
        db: Session,
        kb_id: int,
        wallet_address: str,
        source_path: str,
        rollback_plan: dict[str, dict],
    ) -> None:
        if source_path in rollback_plan:
            return
        document = db.scalar(
            select(ImportedDocument)
            .where(ImportedDocument.kb_id == kb_id)
            .where(ImportedDocument.owner_wallet_address == wallet_address)
            .where(ImportedDocument.source_path == source_path)
        )
        binding = self._find_related_binding(db, kb_id, source_path)
        if document is None:
            rollback_plan[source_path] = {
                "exists": False,
                "source_path": source_path,
                "binding_last_imported_at": binding.last_imported_at if binding is not None else None,
            }
            return

        chunks = list(
            db.scalars(
                select(ImportedChunk)
                .where(ImportedChunk.document_id == document.id)
                .order_by(ImportedChunk.chunk_index.asc(), ImportedChunk.id.asc())
            ).all()
        )
        chunk_ids = [chunk.id for chunk in chunks]
        embeddings = {}
        if chunk_ids:
            for row in db.scalars(
                select(EmbeddingRecord).where(EmbeddingRecord.chunk_id.in_(chunk_ids))
            ).all():
                embeddings[row.chunk_id] = row

        rollback_plan[source_path] = {
            "exists": True,
            "source_path": source_path,
            "binding_last_imported_at": binding.last_imported_at if binding is not None else None,
            "document": {
                "id": document.id,
                "kb_id": document.kb_id,
                "owner_wallet_address": document.owner_wallet_address,
                "source_path": document.source_path,
                "source_file_name": document.source_file_name,
                "source_kind": document.source_kind,
                "source_etag_or_mtime": document.source_etag_or_mtime,
                "parse_status": document.parse_status,
                "chunk_count": document.chunk_count,
                "last_indexed_at": document.last_indexed_at,
                "created_at": document.created_at,
                "updated_at": document.updated_at,
            },
            "chunks": [
                {
                    "chunk": {
                        "id": chunk.id,
                        "document_id": chunk.document_id,
                        "kb_id": chunk.kb_id,
                        "owner_wallet_address": chunk.owner_wallet_address,
                        "chunk_index": chunk.chunk_index,
                        "text": chunk.text,
                        "metadata_json": dict(chunk.metadata_json or {}),
                        "created_at": chunk.created_at,
                    },
                    "embedding": (
                        {
                            "id": embeddings[chunk.id].id,
                            "chunk_id": embeddings[chunk.id].chunk_id,
                            "kb_id": embeddings[chunk.id].kb_id,
                            "owner_wallet_address": embeddings[chunk.id].owner_wallet_address,
                            "vector_id": embeddings[chunk.id].vector_id,
                            "embedding_model": embeddings[chunk.id].embedding_model,
                            "index_status": embeddings[chunk.id].index_status,
                            "vector_json": list(embeddings[chunk.id].vector_json or []),
                            "created_at": embeddings[chunk.id].created_at,
                        }
                        if chunk.id in embeddings
                        else None
                    ),
                }
                for chunk in chunks
            ],
        }

    def _delete_document_state(self, db: Session, document: ImportedDocument) -> None:
        old_vector_ids = [
            row[0]
            for row in db.execute(
                select(EmbeddingRecord.vector_id).where(
                    EmbeddingRecord.chunk_id.in_(select(ImportedChunk.id).where(ImportedChunk.document_id == document.id))
                )
            ).all()
        ]
        self.vector_store.delete_vectors([vector_id for vector_id in old_vector_ids if vector_id])
        db.execute(delete(EmbeddingRecord).where(EmbeddingRecord.chunk_id.in_(select(ImportedChunk.id).where(ImportedChunk.document_id == document.id))))
        db.execute(delete(ImportedChunk).where(ImportedChunk.document_id == document.id))

    def _raise_if_cancel_requested(
        self,
        db: Session,
        task: ImportTask,
        rollback_plan: dict[str, dict],
        rollback_current_transaction: bool = False,
    ) -> None:
        db.refresh(task)
        cancel_requested = task.status in {"cancel_requested", "canceled"}
        if not cancel_requested:
            return
        if rollback_current_transaction:
            db.rollback()
            task = db.get(ImportTask, task.id) or task
        else:
            db.refresh(task)
        if task.status not in {"cancel_requested", "canceled"}:
            task.status = "cancel_requested"
            task.error_message = "cancel requested by user"
            db.commit()
            db.refresh(task)
        raise TaskCanceledError(self._rollback_task(db, task, rollback_plan))

    def _rollback_task(self, db: Session, task: ImportTask, rollback_plan: dict[str, dict]) -> dict:
        restored = 0
        removed = 0
        reindexed_vectors = 0
        restored_paths: list[str] = []
        for source_path, snapshot in reversed(list(rollback_plan.items())):
            current_document = db.scalar(
                select(ImportedDocument)
                .where(ImportedDocument.kb_id == task.kb_id)
                .where(ImportedDocument.owner_wallet_address == task.owner_wallet_address)
                .where(ImportedDocument.source_path == source_path)
            )
            if current_document is not None:
                self._delete_document_state(db, current_document)
                db.delete(current_document)
                removed += 1

            binding = db.scalar(
                select(SourceBinding)
                .where(SourceBinding.kb_id == task.kb_id)
                .where(SourceBinding.source_path == source_path)
            )
            if binding is not None:
                binding.last_imported_at = snapshot.get("binding_last_imported_at")

            if not snapshot.get("exists"):
                restored_paths.append(source_path)
                continue

            document_row = snapshot["document"]
            db.execute(ImportedDocument.__table__.insert().values(**document_row))
            vector_payloads: list[dict] = []
            for entry in snapshot["chunks"]:
                chunk_row = entry["chunk"]
                embedding_row = entry["embedding"]
                db.execute(ImportedChunk.__table__.insert().values(**chunk_row))
                if embedding_row is not None:
                    db.execute(EmbeddingRecord.__table__.insert().values(**embedding_row))
                    vector_payloads.append(
                        {
                            "vector_id": embedding_row["vector_id"],
                            "text": chunk_row["text"],
                            "metadata": {
                                "wallet_address": task.owner_wallet_address,
                                "kb_id": document_row["kb_id"],
                                "document_id": document_row["id"],
                                "chunk_id": chunk_row["id"],
                                "source_path": document_row["source_path"],
                                "source_kind": document_row["source_kind"],
                                "file_name": document_row["source_file_name"],
                                "file_type": chunk_row["metadata_json"].get("file_type"),
                                "chunk_index": chunk_row["chunk_index"],
                                "source_version": document_row["source_etag_or_mtime"],
                                "chunk_strategy": chunk_row["metadata_json"].get("chunk_strategy"),
                            },
                        }
                    )
            if self.settings.vector_store_mode == "weaviate" and vector_payloads:
                self.vector_store.index_chunks(vector_payloads)
                reindexed_vectors += len(vector_payloads)
            restored += 1
            restored_paths.append(source_path)

        self._record_task_item(
            db,
            task_id=task.id,
            source_path=",".join(restored_paths[:3]) if restored_paths else "-",
            file_name="",
            status="rolled_back",
            message="rollback applied after cancel request",
            processed_chunks=0,
            source_version="",
        )
        db.commit()
        return {
            "applied": True,
            "restored_documents": restored,
            "removed_documents": removed,
            "reindexed_vectors": reindexed_vectors,
            "paths": restored_paths,
        }
