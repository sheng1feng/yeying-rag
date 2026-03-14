from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from knowledge.db.base import Base
from knowledge.utils.time import utc_now


def utcnow() -> datetime:
    return utc_now()


class WalletUser(Base):
    __tablename__ = "wallet_users"

    wallet_address: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(ForeignKey("wallet_users.wallet_address"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    retrieval_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    owner: Mapped["WalletUser"] = relationship(back_populates="knowledge_bases")
    bindings: Mapped[list["SourceBinding"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    documents: Mapped[list["ImportedDocument"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")


class SourceBinding(Base):
    __tablename__ = "source_bindings"
    __table_args__ = (UniqueConstraint("kb_id", "source_path", name="uq_kb_source_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="warehouse", nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), default="file", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="bindings")


class ImportedDocument(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("kb_id", "source_path", name="uq_kb_document_source_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_etag_or_mtime: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    parse_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="documents")
    chunks: Mapped[list["ImportedChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class ImportedChunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True, nullable=False)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    document: Mapped["ImportedDocument"] = relationship(back_populates="chunks")
    embedding: Mapped[Optional["EmbeddingRecord"]] = relationship(back_populates="chunk", cascade="all, delete-orphan", uselist=False)


class EmbeddingRecord(Base):
    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunks.id"), index=True, nullable=False, unique=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    vector_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    index_status: Mapped[str] = mapped_column(String(32), default="indexed", nullable=False)
    vector_json: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    chunk: Mapped["ImportedChunk"] = relationship(back_populates="embedding")


class ImportTask(Base):
    __tablename__ = "import_tasks"
    __table_args__ = (
        Index("ix_import_tasks_status_created_at", "status", "created_at"),
        Index("ix_import_tasks_owner_status_created_at", "owner_wallet_address", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    source_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    stats_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    attempt: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ImportTaskItem(Base):
    __tablename__ = "import_task_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("import_tasks.id"), index=True, nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    processed_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class WorkerStatus(Base):
    __tablename__ = "worker_status"

    worker_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)


class UploadRecord(Base):
    __tablename__ = "upload_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    warehouse_target_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="uploaded", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class WarehouseCredential(Base):
    __tablename__ = "warehouse_credentials"

    owner_wallet_address: Mapped[str] = mapped_column(String(64), primary_key=True)
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    warehouse_base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class WarehouseUcanBootstrap(Base):
    __tablename__ = "warehouse_ucan_bootstraps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(String(255), nullable=False)
    cap_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    root_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class WarehouseUcanCredential(Base):
    __tablename__ = "warehouse_ucan_credentials"

    owner_wallet_address: Mapped[str] = mapped_column(String(64), primary_key=True)
    encrypted_session_private_key: Mapped[str] = mapped_column(Text, nullable=False)
    session_did: Mapped[str] = mapped_column(String(255), nullable=False)
    audience: Mapped[str] = mapped_column(String(255), nullable=False)
    cap_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    root_proof_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    root_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class WarehouseAppUcanCredential(Base):
    __tablename__ = "warehouse_app_ucan_credentials"

    owner_wallet_address: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    encrypted_session_private_key: Mapped[str] = mapped_column(Text, nullable=False)
    session_did: Mapped[str] = mapped_column(String(255), nullable=False)
    audience: Mapped[str] = mapped_column(String(255), nullable=False)
    cap_json: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    root_proof_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    root_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class LongTermMemory(Base):
    __tablename__ = "memories_long_term"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    kb_id: Mapped[Optional[int]] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=True)
    category: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="bot", nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ShortTermMemory(Base):
    __tablename__ = "memories_short_term"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ttl_or_expire_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class MemoryIngestionEvent(Base):
    __tablename__ = "memory_ingestion_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    kb_id: Mapped[Optional[int]] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="bot", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="completed", nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    query_preview: Mapped[str] = mapped_column(Text, default="", nullable=False)
    answer_preview: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    short_term_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    long_term_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
