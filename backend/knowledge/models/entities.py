from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from knowledge.db.base import Base
from knowledge.utils.time import utc_now


def utcnow() -> datetime:
    return utc_now()


SOURCE_SYNC_STATUSES = ("pending_sync", "syncing", "synced", "failed", "source_missing", "disabled")
SOURCE_MISSING_POLICIES = ("mark_missing", "retain_index_until_confirmed")
SOURCE_ASSET_AVAILABILITY_STATUSES = ("discovered", "available", "changed", "missing", "missing_unconfirmed", "ignored")
EVIDENCE_VECTOR_STATUSES = ("pending", "indexed", "failed")
KNOWLEDGE_ITEM_ORIGIN_TYPES = ("extracted", "manual", "manual_from_extracted", "merged")
KNOWLEDGE_ITEM_CANDIDATE_REVIEW_STATUSES = ("pending_review", "accepted", "rejected", "merged")
KNOWLEDGE_ITEM_LIFECYCLE_STATUSES = ("candidate", "confirmed", "rejected", "archived")
KNOWLEDGE_ITEM_REVISION_REVIEW_STATUSES = ("draft", "ready_for_review", "accepted", "rejected")
KNOWLEDGE_ITEM_REVISION_VISIBILITY_STATUSES = ("active", "archived", "hotfix")
KB_RELEASE_STATUSES = ("preparing", "published", "superseded", "rolled_back")
SERVICE_PRINCIPAL_STATUSES = ("active", "disabled", "revoked")
SERVICE_GRANT_STATUSES = ("active", "expired", "revoked", "suspended")
SERVICE_GRANT_RELEASE_SELECTION_MODES = ("latest_published", "pinned_release")
RETRIEVAL_QUERY_MODES = ("formal_first", "formal_only", "evidence_only", "audit", "search_lab_compare")
WAREHOUSE_ACCESS_CREDENTIAL_KINDS = ("read", "read_write")
WAREHOUSE_ACCESS_CREDENTIAL_STATUSES = ("active", "invalid", "revoked_local")


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
    sources: Mapped[list["Source"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    source_assets: Mapped[list["SourceAsset"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    evidence_units: Mapped[list["EvidenceUnit"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    knowledge_item_candidates: Mapped[list["KnowledgeItemCandidate"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    knowledge_items: Mapped[list["KnowledgeItem"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    releases: Mapped[list["KBRelease"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    service_grants: Mapped[list["ServiceGrant"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    retrieval_logs: Mapped[list["RetrievalLog"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")


class SourceBinding(Base):
    __tablename__ = "source_bindings"
    __table_args__ = (UniqueConstraint("kb_id", "source_path", name="uq_kb_source_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="warehouse", nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), default="file", nullable=False)
    credential_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
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


class WarehouseAccessCredential(Base):
    __tablename__ = "warehouse_access_credentials"
    __table_args__ = (
        UniqueConstraint(
            "owner_wallet_address",
            "credential_kind",
            "key_id",
            "root_path",
            name="uq_warehouse_access_credential_owner_kind_key_root",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    credential_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_key_secret: Mapped[str] = mapped_column(Text, nullable=False)
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("kb_id", "source_path", name="uq_sources_kb_source_path"),
        Index("ix_sources_kb_sync_status", "kb_id", "sync_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="warehouse", nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), default="directory", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_status: Mapped[str] = mapped_column(String(32), default=SOURCE_SYNC_STATUSES[0], nullable=False)
    missing_policy: Mapped[str] = mapped_column(String(64), default=SOURCE_MISSING_POLICIES[0], nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="sources")
    assets: Mapped[list["SourceAsset"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class SourceAsset(Base):
    __tablename__ = "source_assets"
    __table_args__ = (
        UniqueConstraint("source_id", "asset_path", name="uq_source_assets_source_asset_path"),
        Index("ix_source_assets_kb_availability_status", "kb_id", "availability_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True, nullable=False)
    asset_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    asset_name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), default="file", nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    availability_status: Mapped[str] = mapped_column(String(32), default=SOURCE_ASSET_AVAILABILITY_STATUSES[0], nullable=False)
    last_ingested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="source_assets")
    source: Mapped["Source"] = relationship(back_populates="assets")
    evidence_units: Mapped[list["EvidenceUnit"]] = relationship(back_populates="asset", cascade="all, delete-orphan")


class EvidenceUnit(Base):
    __tablename__ = "evidence_units"
    __table_args__ = (
        Index("ix_evidence_units_kb_vector_status", "kb_id", "vector_status"),
        Index("ix_evidence_units_asset_id_created_at", "asset_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("source_assets.id"), index=True, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), default="text_span", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_locator: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    vector_status: Mapped[str] = mapped_column(String(32), default=EVIDENCE_VECTOR_STATUSES[0], nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="evidence_units")
    asset: Mapped["SourceAsset"] = relationship(back_populates="evidence_units")
    knowledge_item_links: Mapped[list["KnowledgeItemEvidenceLink"]] = relationship(back_populates="evidence_unit", cascade="all, delete-orphan")


class KnowledgeItemCandidate(Base):
    __tablename__ = "knowledge_item_candidates"
    __table_args__ = (
        Index("ix_knowledge_item_candidates_kb_review_status", "kb_id", "review_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    structured_payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    item_contract_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    origin_type: Mapped[str] = mapped_column(String(32), default=KNOWLEDGE_ITEM_ORIGIN_TYPES[0], nullable=False)
    origin_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(String(32), default=KNOWLEDGE_ITEM_CANDIDATE_REVIEW_STATUSES[0], nullable=False)
    created_from_job_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="knowledge_item_candidates")


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        Index("ix_knowledge_items_kb_lifecycle_status", "kb_id", "lifecycle_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_type: Mapped[str] = mapped_column(String(32), default=KNOWLEDGE_ITEM_ORIGIN_TYPES[0], nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="confirmed", nullable=False)
    current_revision_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    is_hotfix: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="knowledge_items")
    revisions: Mapped[list["KnowledgeItemRevision"]] = relationship(back_populates="knowledge_item", cascade="all, delete-orphan")
    release_items: Mapped[list["KBReleaseItem"]] = relationship(back_populates="knowledge_item", cascade="all, delete-orphan")


class KnowledgeItemRevision(Base):
    __tablename__ = "knowledge_item_revisions"
    __table_args__ = (
        UniqueConstraint("knowledge_item_id", "revision_no", name="uq_knowledge_item_revisions_item_revision_no"),
        Index("ix_knowledge_item_revisions_item_review_visibility", "knowledge_item_id", "review_status", "visibility_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_item_id: Mapped[int] = mapped_column(ForeignKey("knowledge_items.id"), index=True, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    structured_payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    item_contract_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), default=KNOWLEDGE_ITEM_REVISION_REVIEW_STATUSES[0], nullable=False)
    visibility_status: Mapped[str] = mapped_column(String(32), default=KNOWLEDGE_ITEM_REVISION_VISIBILITY_STATUSES[0], nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    provenance_type: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    applicability_scope_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_workspace_head: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    knowledge_item: Mapped["KnowledgeItem"] = relationship(back_populates="revisions")
    evidence_links: Mapped[list["KnowledgeItemEvidenceLink"]] = relationship(back_populates="knowledge_item_revision", cascade="all, delete-orphan")
    release_items: Mapped[list["KBReleaseItem"]] = relationship(back_populates="knowledge_item_revision", cascade="all, delete-orphan")


class KnowledgeItemEvidenceLink(Base):
    __tablename__ = "knowledge_item_evidence_links"
    __table_args__ = (
        UniqueConstraint("knowledge_item_revision_id", "evidence_unit_id", "role", name="uq_item_revision_evidence_role"),
        Index("ix_item_evidence_links_revision_rank", "knowledge_item_revision_id", "rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_item_revision_id: Mapped[int] = mapped_column(ForeignKey("knowledge_item_revisions.id"), index=True, nullable=False)
    evidence_unit_id: Mapped[int] = mapped_column(ForeignKey("evidence_units.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="supporting", nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)

    knowledge_item_revision: Mapped["KnowledgeItemRevision"] = relationship(back_populates="evidence_links")
    evidence_unit: Mapped["EvidenceUnit"] = relationship(back_populates="knowledge_item_links")


class KBRelease(Base):
    __tablename__ = "kb_releases"
    __table_args__ = (
        UniqueConstraint("kb_id", "version", name="uq_kb_releases_kb_version"),
        Index("ix_kb_releases_kb_status_published_at", "kb_id", "status", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=KB_RELEASE_STATUSES[0], nullable=False)
    release_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    supersedes_release_id: Mapped[Optional[int]] = mapped_column(ForeignKey("kb_releases.id"), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="releases")
    supersedes_release: Mapped[Optional["KBRelease"]] = relationship(remote_side="KBRelease.id")
    release_items: Mapped[list["KBReleaseItem"]] = relationship(back_populates="release", cascade="all, delete-orphan")
    pinned_grants: Mapped[list["ServiceGrant"]] = relationship(back_populates="pinned_release")
    retrieval_logs: Mapped[list["RetrievalLog"]] = relationship(back_populates="release")


class KBReleaseItem(Base):
    __tablename__ = "kb_release_items"
    __table_args__ = (
        UniqueConstraint("release_id", "knowledge_item_id", name="uq_kb_release_items_release_item"),
        UniqueConstraint("release_id", "knowledge_item_revision_id", name="uq_kb_release_items_release_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("kb_releases.id"), index=True, nullable=False)
    knowledge_item_id: Mapped[int] = mapped_column(ForeignKey("knowledge_items.id"), index=True, nullable=False)
    knowledge_item_revision_id: Mapped[int] = mapped_column(ForeignKey("knowledge_item_revisions.id"), index=True, nullable=False)
    item_version_hash: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    content_health_status: Mapped[str] = mapped_column(String(32), default="healthy", nullable=False)

    release: Mapped["KBRelease"] = relationship(back_populates="release_items")
    knowledge_item: Mapped["KnowledgeItem"] = relationship(back_populates="release_items")
    knowledge_item_revision: Mapped["KnowledgeItemRevision"] = relationship(back_populates="release_items")


class ServicePrincipal(Base):
    __tablename__ = "service_principals"
    __table_args__ = (
        UniqueConstraint("owner_wallet_address", "service_id", name="uq_service_principals_owner_service_id"),
        Index("ix_service_principals_owner_status", "owner_wallet_address", "principal_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(ForeignKey("wallet_users.wallet_address"), index=True, nullable=False)
    service_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_fingerprint: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    public_key_jwk: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    principal_status: Mapped[str] = mapped_column(String(32), default=SERVICE_PRINCIPAL_STATUSES[0], nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    grants: Mapped[list["ServiceGrant"]] = relationship(back_populates="service_principal", cascade="all, delete-orphan")
    retrieval_logs: Mapped[list["RetrievalLog"]] = relationship(back_populates="service_principal")


class ServiceGrant(Base):
    __tablename__ = "service_grants"
    __table_args__ = (
        UniqueConstraint("kb_id", "service_principal_id", name="uq_service_grants_kb_principal"),
        Index("ix_service_grants_owner_status_expires_at", "owner_wallet_address", "grant_status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(ForeignKey("wallet_users.wallet_address"), index=True, nullable=False)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=False)
    service_principal_id: Mapped[int] = mapped_column(ForeignKey("service_principals.id"), index=True, nullable=False)
    grant_status: Mapped[str] = mapped_column(String(32), default=SERVICE_GRANT_STATUSES[0], nullable=False)
    release_selection_mode: Mapped[str] = mapped_column(String(32), default=SERVICE_GRANT_RELEASE_SELECTION_MODES[0], nullable=False)
    pinned_release_id: Mapped[Optional[int]] = mapped_column(ForeignKey("kb_releases.id"), index=True, nullable=True)
    default_result_mode: Mapped[str] = mapped_column(String(32), default="compact", nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    knowledge_base: Mapped["KnowledgeBase"] = relationship(back_populates="service_grants")
    service_principal: Mapped["ServicePrincipal"] = relationship(back_populates="grants")
    pinned_release: Mapped[Optional["KBRelease"]] = relationship(back_populates="pinned_grants")
    retrieval_logs: Mapped[list["RetrievalLog"]] = relationship(back_populates="service_grant")


class RetrievalLog(Base):
    __tablename__ = "retrieval_logs"
    __table_args__ = (
        Index("ix_retrieval_logs_owner_created_at", "owner_wallet_address", "created_at"),
        Index("ix_retrieval_logs_kb_created_at", "kb_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_wallet_address: Mapped[str] = mapped_column(ForeignKey("wallet_users.wallet_address"), index=True, nullable=False)
    kb_id: Mapped[Optional[int]] = mapped_column(ForeignKey("knowledge_bases.id"), index=True, nullable=True)
    service_grant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("service_grants.id"), index=True, nullable=True)
    service_principal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("service_principals.id"), index=True, nullable=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_mode: Mapped[str] = mapped_column(String(32), default=RETRIEVAL_QUERY_MODES[0], nullable=False)
    release_id: Mapped[Optional[int]] = mapped_column(ForeignKey("kb_releases.id"), index=True, nullable=True)
    result_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    trace_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    knowledge_base: Mapped[Optional["KnowledgeBase"]] = relationship(back_populates="retrieval_logs")
    service_grant: Mapped[Optional["ServiceGrant"]] = relationship(back_populates="retrieval_logs")
    service_principal: Mapped[Optional["ServicePrincipal"]] = relationship(back_populates="retrieval_logs")
    release: Mapped[Optional["KBRelease"]] = relationship(back_populates="retrieval_logs")
