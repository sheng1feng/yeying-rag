from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from knowledge.models import LongTermMemory, MemoryIngestionEvent, ShortTermMemory
from knowledge.utils.time import utc_now


class MemoryService:
    def list_long_term(self, db: Session, wallet_address: str) -> list[LongTermMemory]:
        return list(
            db.scalars(
                select(LongTermMemory)
                .where(LongTermMemory.owner_wallet_address == wallet_address)
                .order_by(LongTermMemory.created_at.desc())
            ).all()
        )

    def create_long_term(self, db: Session, wallet_address: str, payload: dict) -> LongTermMemory:
        memory = LongTermMemory(owner_wallet_address=wallet_address, **payload)
        db.add(memory)
        db.commit()
        db.refresh(memory)
        return memory

    def update_long_term(self, db: Session, wallet_address: str, memory_id: int, payload: dict) -> LongTermMemory:
        memory = db.get(LongTermMemory, memory_id)
        if memory is None or memory.owner_wallet_address != wallet_address:
            raise ValueError("memory not found")
        for key, value in payload.items():
            setattr(memory, key, value)
        db.commit()
        db.refresh(memory)
        return memory

    def delete_long_term(self, db: Session, wallet_address: str, memory_id: int) -> None:
        memory = db.get(LongTermMemory, memory_id)
        if memory is None or memory.owner_wallet_address != wallet_address:
            raise ValueError("memory not found")
        self._record_manual_delete_event(
            db=db,
            wallet_address=wallet_address,
            memory_kind="long_term",
            memory_id=memory.id,
            session_id="console",
            kb_id=memory.kb_id,
            content=memory.content,
            extra_notes={
                "category": memory.category,
                "memory_source": memory.source,
                "score": memory.score,
            },
        )
        db.delete(memory)
        db.commit()

    def list_short_term(self, db: Session, wallet_address: str, session_id: str | None = None) -> list[ShortTermMemory]:
        stmt = (
            select(ShortTermMemory)
            .where(ShortTermMemory.owner_wallet_address == wallet_address)
            .where(or_(ShortTermMemory.ttl_or_expire_at.is_(None), ShortTermMemory.ttl_or_expire_at >= utc_now()))
            .order_by(ShortTermMemory.created_at.desc())
        )
        if session_id:
            stmt = stmt.where(ShortTermMemory.session_id == session_id)
        return list(db.scalars(stmt).all())

    def create_short_term(self, db: Session, wallet_address: str, payload: dict) -> ShortTermMemory:
        memory = ShortTermMemory(owner_wallet_address=wallet_address, **payload)
        db.add(memory)
        db.commit()
        db.refresh(memory)
        return memory

    def delete_short_term(self, db: Session, wallet_address: str, memory_id: int) -> None:
        memory = db.get(ShortTermMemory, memory_id)
        if memory is None or memory.owner_wallet_address != wallet_address:
            raise ValueError("memory not found")
        self._record_manual_delete_event(
            db=db,
            wallet_address=wallet_address,
            memory_kind="short_term",
            memory_id=memory.id,
            session_id=memory.session_id,
            kb_id=None,
            content=memory.content,
            extra_notes={
                "memory_type": memory.memory_type,
                "ttl_or_expire_at": memory.ttl_or_expire_at.isoformat() if memory.ttl_or_expire_at else "",
            },
        )
        db.delete(memory)
        db.commit()

    def cleanup_expired_short_term(self, db: Session) -> int:
        result = db.execute(
            delete(ShortTermMemory)
            .where(ShortTermMemory.ttl_or_expire_at.is_not(None))
            .where(ShortTermMemory.ttl_or_expire_at < utc_now())
        )
        db.commit()
        return result.rowcount or 0

    def _record_manual_delete_event(
        self,
        db: Session,
        wallet_address: str,
        memory_kind: str,
        memory_id: int,
        session_id: str,
        kb_id: int | None,
        content: str,
        extra_notes: dict,
    ) -> None:
        db.add(
            MemoryIngestionEvent(
                owner_wallet_address=wallet_address,
                session_id=session_id or "console",
                kb_id=kb_id,
                source="console",
                status="deleted",
                trace_id=f"console-delete-{memory_kind}-{memory_id}",
                query_preview=f"删除{'长期' if memory_kind == 'long_term' else '短期'}记忆 #{memory_id}",
                answer_preview=self._preview(content),
                source_refs_json=[],
                short_term_created=0,
                long_term_created=0,
                notes_json={
                    "operation": f"delete_{memory_kind}",
                    "memory_kind": memory_kind,
                    "memory_id": memory_id,
                    "deleted_short_term": 1 if memory_kind == "short_term" else 0,
                    "deleted_long_term": 1 if memory_kind == "long_term" else 0,
                    "content_preview": self._preview(content, limit=240),
                    **extra_notes,
                },
            )
        )

    @staticmethod
    def _preview(content: str, limit: int = 160) -> str:
        value = " ".join(str(content or "").split()).strip()
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)].rstrip() + "…"
