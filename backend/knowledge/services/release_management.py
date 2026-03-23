from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from knowledge.models import KBRelease, KBReleaseItem, KnowledgeBase, KnowledgeItem, KnowledgeItemRevision
from knowledge.utils.time import utc_now


class ReleaseManagementService:
    def publish_workspace_release(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        *,
        version: str,
        release_note: str = "",
    ) -> KBRelease:
        kb = self._get_kb_or_404(db, wallet_address, kb_id)
        release_pairs = self._current_workspace_pairs(db, kb.id)
        if not release_pairs:
            raise ValueError("no confirmed knowledge items available for release")
        current_release = self._get_current_published_release(db, kb.id)
        next_release = self._create_release(
            db,
            kb_id=kb.id,
            version=version,
            release_note=release_note,
            created_by=wallet_address,
            supersedes_release_id=current_release.id if current_release is not None else None,
        )
        self._replace_current_release_status(current_release, "superseded")
        self._create_release_items(db, next_release, release_pairs)
        db.commit()
        db.refresh(next_release)
        return next_release

    def create_hotfix_release(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        *,
        version: str,
        release_note: str = "",
        knowledge_item_ids: list[int],
        base_release_id: int | None = None,
    ) -> KBRelease:
        kb = self._get_kb_or_404(db, wallet_address, kb_id)
        item_ids = [int(item_id) for item_id in dict.fromkeys(knowledge_item_ids or []) if int(item_id) > 0]
        if not item_ids:
            raise ValueError("knowledge_item_ids cannot be empty for hotfix")
        base_release = self._resolve_base_release(db, kb.id, base_release_id)
        current_release = self._get_current_published_release(db, kb.id)
        base_pairs = self._release_pairs(db, base_release.id)
        base_by_item_id = {item.id: (item, revision) for item, revision in base_pairs}
        for item_id in item_ids:
            item = self._get_item_for_kb(db, kb.id, item_id)
            revision = self._get_revision_for_item(db, item)
            base_by_item_id[item.id] = (item, revision)
        next_release = self._create_release(
            db,
            kb_id=kb.id,
            version=version,
            release_note=release_note,
            created_by=wallet_address,
            supersedes_release_id=current_release.id if current_release is not None else None,
        )
        self._replace_current_release_status(current_release, "superseded")
        self._create_release_items(db, next_release, list(base_by_item_id.values()))
        db.commit()
        db.refresh(next_release)
        return next_release

    def rollback_to_release(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        *,
        target_release_id: int,
        version: str,
        release_note: str = "",
    ) -> KBRelease:
        kb = self._get_kb_or_404(db, wallet_address, kb_id)
        target_release = self.get_release_or_404(db, kb.id, target_release_id)
        target_pairs = self._release_pairs(db, target_release.id)
        if not target_pairs:
            raise ValueError("target release has no release items")
        current_release = self._get_current_published_release(db, kb.id)
        next_release = self._create_release(
            db,
            kb_id=kb.id,
            version=version,
            release_note=release_note,
            created_by=wallet_address,
            supersedes_release_id=current_release.id if current_release is not None else None,
        )
        self._replace_current_release_status(current_release, "rolled_back")
        self._create_release_items(db, next_release, target_pairs)
        db.commit()
        db.refresh(next_release)
        return next_release

    def list_releases(self, db: Session, wallet_address: str, kb_id: int) -> list[KBRelease]:
        self._get_kb_or_404(db, wallet_address, kb_id)
        return list(
            db.scalars(
                select(KBRelease)
                .where(KBRelease.kb_id == kb_id)
                .order_by(KBRelease.created_at.desc(), KBRelease.id.desc())
            ).all()
        )

    def get_release_or_404(self, db: Session, kb_id: int, release_id: int) -> KBRelease:
        release = db.scalar(
            select(KBRelease)
            .options(selectinload(KBRelease.release_items))
            .where(KBRelease.kb_id == kb_id)
            .where(KBRelease.id == release_id)
        )
        if release is None:
            raise LookupError("release not found")
        return release

    def get_current_release_or_404(self, db: Session, wallet_address: str, kb_id: int) -> KBRelease:
        self._get_kb_or_404(db, wallet_address, kb_id)
        release = self._get_current_published_release(db, kb_id)
        if release is None:
            raise LookupError("current published release not found")
        return release

    def list_release_items(self, db: Session, release_id: int) -> list[KBReleaseItem]:
        return list(
            db.scalars(
                select(KBReleaseItem)
                .where(KBReleaseItem.release_id == release_id)
                .order_by(KBReleaseItem.knowledge_item_id.asc(), KBReleaseItem.id.asc())
            ).all()
        )

    def _get_current_published_release(self, db: Session, kb_id: int) -> KBRelease | None:
        return db.scalar(
            select(KBRelease)
            .where(KBRelease.kb_id == kb_id)
            .where(KBRelease.status == "published")
            .order_by(KBRelease.published_at.desc().nulls_last(), KBRelease.created_at.desc(), KBRelease.id.desc())
        )

    def _create_release(
        self,
        db: Session,
        *,
        kb_id: int,
        version: str,
        release_note: str,
        created_by: str,
        supersedes_release_id: int | None,
    ) -> KBRelease:
        normalized_version = str(version or "").strip()
        if not normalized_version:
            raise ValueError("version cannot be empty")
        existing = db.scalar(select(KBRelease).where(KBRelease.kb_id == kb_id).where(KBRelease.version == normalized_version))
        if existing is not None:
            raise ValueError("release version already exists for knowledge base")
        release = KBRelease(
            kb_id=kb_id,
            version=normalized_version,
            status="published",
            release_note=release_note,
            published_at=utc_now(),
            created_by=created_by,
            supersedes_release_id=supersedes_release_id,
        )
        db.add(release)
        db.flush()
        return release

    def _create_release_items(
        self,
        db: Session,
        release: KBRelease,
        item_revision_pairs: list[tuple[KnowledgeItem, KnowledgeItemRevision]],
    ) -> None:
        for item, revision in item_revision_pairs:
            db.add(
                KBReleaseItem(
                    release_id=release.id,
                    knowledge_item_id=item.id,
                    knowledge_item_revision_id=revision.id,
                    item_version_hash=self._item_version_hash(revision),
                    content_health_status="healthy",
                )
            )
        db.flush()

    @staticmethod
    def _replace_current_release_status(current_release: KBRelease | None, next_status: str) -> None:
        if current_release is None:
            return
        current_release.status = next_status

    def _current_workspace_pairs(self, db: Session, kb_id: int) -> list[tuple[KnowledgeItem, KnowledgeItemRevision]]:
        items = list(
            db.scalars(
                select(KnowledgeItem)
                .where(KnowledgeItem.kb_id == kb_id)
                .where(KnowledgeItem.lifecycle_status == "confirmed")
                .order_by(KnowledgeItem.id.asc())
            ).all()
        )
        pairs: list[tuple[KnowledgeItem, KnowledgeItemRevision]] = []
        for item in items:
            if item.current_revision_id is None:
                continue
            revision = self._get_revision_for_item(db, item)
            pairs.append((item, revision))
        return pairs

    def _release_pairs(self, db: Session, release_id: int) -> list[tuple[KnowledgeItem, KnowledgeItemRevision]]:
        rows = db.execute(
            select(KBReleaseItem, KnowledgeItem, KnowledgeItemRevision)
            .join(KnowledgeItem, KnowledgeItem.id == KBReleaseItem.knowledge_item_id)
            .join(KnowledgeItemRevision, KnowledgeItemRevision.id == KBReleaseItem.knowledge_item_revision_id)
            .where(KBReleaseItem.release_id == release_id)
            .order_by(KBReleaseItem.knowledge_item_id.asc(), KBReleaseItem.id.asc())
        ).all()
        return [(item, revision) for _release_item, item, revision in rows]

    def _resolve_base_release(self, db: Session, kb_id: int, base_release_id: int | None) -> KBRelease:
        if base_release_id is not None:
            return self.get_release_or_404(db, kb_id, base_release_id)
        current_release = self._get_current_published_release(db, kb_id)
        if current_release is None:
            raise ValueError("base release not found for hotfix")
        return current_release

    def _get_item_for_kb(self, db: Session, kb_id: int, item_id: int) -> KnowledgeItem:
        item = db.get(KnowledgeItem, item_id)
        if item is None or item.kb_id != kb_id:
            raise ValueError(f"knowledge item {item_id} not found in knowledge base")
        if item.lifecycle_status != "confirmed":
            raise ValueError(f"knowledge item {item_id} is not confirmed")
        return item

    @staticmethod
    def _get_revision_for_item(db: Session, item: KnowledgeItem) -> KnowledgeItemRevision:
        if item.current_revision_id is None:
            raise ValueError(f"knowledge item {item.id} has no current revision")
        revision = db.get(KnowledgeItemRevision, item.current_revision_id)
        if revision is None or revision.knowledge_item_id != item.id:
            raise ValueError(f"knowledge item {item.id} current revision not found")
        return revision

    @staticmethod
    def _item_version_hash(revision: KnowledgeItemRevision) -> str:
        raw = json.dumps(
            {
                "revision_id": revision.id,
                "title": revision.title,
                "statement": revision.statement,
                "structured_payload_json": revision.structured_payload_json,
                "item_contract_version": revision.item_contract_version,
                "review_status": revision.review_status,
                "visibility_status": revision.visibility_status,
                "effective_from": revision.effective_from.isoformat() if revision.effective_from else None,
                "effective_to": revision.effective_to.isoformat() if revision.effective_to else None,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _get_kb_or_404(db: Session, wallet_address: str, kb_id: int) -> KnowledgeBase:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.owner_wallet_address != wallet_address:
            raise LookupError("knowledge base not found")
        return kb
