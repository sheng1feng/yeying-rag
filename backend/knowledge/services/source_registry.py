from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.models import KnowledgeBase, Source
from knowledge.models.entities import SOURCE_MISSING_POLICIES
from knowledge.services.warehouse_scope import ensure_current_app_path


class SourceRegistryService:
    def get_kb_or_404(self, db: Session, wallet_address: str, kb_id: int) -> KnowledgeBase:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.owner_wallet_address != wallet_address:
            raise LookupError("knowledge base not found")
        return kb

    def get_source_or_404(self, db: Session, wallet_address: str, kb_id: int, source_id: int) -> Source:
        kb = self.get_kb_or_404(db, wallet_address, kb_id)
        source = db.get(Source, source_id)
        if source is None or source.kb_id != kb.id:
            raise LookupError("source not found")
        return source

    def list_sources(self, db: Session, wallet_address: str, kb_id: int) -> list[Source]:
        self.get_kb_or_404(db, wallet_address, kb_id)
        return list(
            db.scalars(
                select(Source)
                .where(Source.kb_id == kb_id)
                .order_by(Source.created_at.asc(), Source.id.asc())
            ).all()
        )

    def create_source(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        source_type: str,
        source_path: str,
        scope_type: str,
        enabled: bool,
        missing_policy: str,
    ) -> Source:
        kb = self.get_kb_or_404(db, wallet_address, kb_id)
        normalized_path = ensure_current_app_path(source_path, "source_path")
        normalized_missing_policy = self._validate_missing_policy(missing_policy)
        existing = db.scalar(
            select(Source)
            .where(Source.kb_id == kb.id)
            .where(Source.source_path == normalized_path)
        )
        if existing is not None:
            return existing
        source = Source(
            kb_id=kb.id,
            source_type=str(source_type or "warehouse").strip() or "warehouse",
            source_path=normalized_path,
            scope_type=str(scope_type or "directory").strip() or "directory",
            enabled=bool(enabled),
            missing_policy=normalized_missing_policy,
            sync_status="pending_sync" if enabled else "disabled",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    def update_source(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        source_id: int,
        *,
        enabled: bool | None = None,
        missing_policy: str | None = None,
    ) -> Source:
        source = self.get_source_or_404(db, wallet_address, kb_id, source_id)
        if enabled is not None:
            source.enabled = bool(enabled)
            if not source.enabled:
                source.sync_status = "disabled"
            elif source.sync_status == "disabled":
                source.sync_status = "pending_sync"
        if missing_policy is not None:
            source.missing_policy = self._validate_missing_policy(missing_policy)
        db.commit()
        db.refresh(source)
        return source

    @staticmethod
    def _validate_missing_policy(missing_policy: str | None) -> str:
        normalized = str(missing_policy or SOURCE_MISSING_POLICIES[0]).strip() or SOURCE_MISSING_POLICIES[0]
        if normalized not in SOURCE_MISSING_POLICIES:
            raise ValueError(f"missing_policy must be one of: {', '.join(SOURCE_MISSING_POLICIES)}")
        return normalized
