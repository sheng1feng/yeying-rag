from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.models import LongTermMemory, MemoryIngestionEvent, ShortTermMemory
from knowledge.services.conversation import build_memory_session_key, parse_memory_session_key
from knowledge.utils.time import utc_now


PREFERENCE_PATTERNS = [
    re.compile(r"(?:我|用户)(?:喜欢|偏好|习惯|常用|默认|希望|倾向于|不要|避免|优先)\s*([^。！？!?;\n]{2,80})"),
    re.compile(r"请(?:用|保持|优先|避免)\s*([^。！？!?;\n]{2,80})"),
    re.compile(r"(?:prefer|usually|default to|avoid|always use)\s+([^.!?\n]{2,80})", re.IGNORECASE),
]


class MemoryIngestionService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def list_events(
        self,
        db: Session,
        wallet_address: str,
        session_id: str | None = None,
        trace_id: str | None = None,
        limit: int = 20,
    ) -> list[MemoryIngestionEvent]:
        stmt = (
            select(MemoryIngestionEvent)
            .where(MemoryIngestionEvent.owner_wallet_address == wallet_address)
            .order_by(MemoryIngestionEvent.created_at.desc())
            .limit(limit)
        )
        if session_id:
            stmt = stmt.where(MemoryIngestionEvent.session_id == session_id)
        if trace_id:
            stmt = stmt.where(MemoryIngestionEvent.trace_id == trace_id)
        return list(db.scalars(stmt).all())

    def ingest(self, db: Session, wallet_address: str, payload: dict) -> dict:
        session_id = self._clean(payload.get("session_id"))
        memory_namespace = self._clean(payload.get("memory_namespace"))
        query = self._clean(payload.get("query"))
        answer = self._clean(payload.get("answer"))
        source = self._clean(payload.get("source")) or "bot"
        trace_id = self._clean(payload.get("trace_id"))
        kb_id = payload.get("kb_id")
        if not session_id:
            raise ValueError("session_id 不能为空")
        if not query:
            raise ValueError("query 不能为空")
        memory_session_key = build_memory_session_key(session_id=session_id, memory_namespace=memory_namespace)

        now = utc_now()
        source_refs = self._dedupe_items(payload.get("source_refs") or [], limit=10)
        explicit_long = self._dedupe_items(payload.get("long_term_candidates") or [], limit=self.settings.auto_memory_max_long_terms)
        explicit_short = self._dedupe_items(payload.get("short_term_candidates") or [], limit=self.settings.auto_memory_max_short_terms)

        created_short: list[ShortTermMemory] = []
        created_long: list[LongTermMemory] = []
        skipped_short: list[str] = []
        skipped_long: list[str] = []

        if payload.get("persist_recent_turn", True):
            recent_turn = self._compose_recent_turn(query, answer, source_refs)
            memory = self._create_short_term_if_new(
                db=db,
                wallet_address=wallet_address,
                session_id=memory_session_key or session_id,
                memory_type="recent_turn",
                content=recent_turn,
                ttl=now + timedelta(hours=self.settings.auto_memory_short_term_ttl_hours),
            )
            if memory is None:
                skipped_short.append(recent_turn)
            else:
                created_short.append(memory)

        if source_refs:
            refs_summary = f"本轮检索使用知识源：{', '.join(source_refs[:3])}"
            memory = self._create_short_term_if_new(
                db=db,
                wallet_address=wallet_address,
                session_id=memory_session_key or session_id,
                memory_type="summary",
                content=refs_summary,
                ttl=now + timedelta(hours=self.settings.auto_memory_short_term_ttl_hours),
            )
            if memory is None:
                skipped_short.append(refs_summary)
            else:
                created_short.append(memory)

        for candidate in explicit_short:
            memory = self._create_short_term_if_new(
                db=db,
                wallet_address=wallet_address,
                session_id=memory_session_key or session_id,
                memory_type="temporary_fact",
                content=candidate,
                ttl=now + timedelta(hours=self.settings.auto_memory_short_term_ttl_hours),
            )
            if memory is None:
                skipped_short.append(candidate)
            else:
                created_short.append(memory)

        long_term_candidates = self._dedupe_items(
            explicit_long + self._extract_long_term_candidates(query, answer),
            limit=self.settings.auto_memory_max_long_terms,
        )
        for candidate in long_term_candidates:
            category = "preference" if candidate.startswith("用户偏好：") else "fact"
            content = candidate if candidate.startswith("用户偏好：") else f"用户事实：{candidate}"
            memory = self._create_long_term_if_new(
                db=db,
                wallet_address=wallet_address,
                kb_id=kb_id,
                category=category,
                content=content,
                source=f"{source}:auto-ingest",
                score=85,
            )
            if memory is None:
                skipped_long.append(content)
            else:
                created_long.append(memory)

        event = MemoryIngestionEvent(
            owner_wallet_address=wallet_address,
            session_id=session_id,
            kb_id=kb_id,
            source=source,
            status="completed",
            trace_id=trace_id,
            query_preview=self._truncate(query, 240),
            answer_preview=self._truncate(answer, 320),
            source_refs_json=source_refs,
            short_term_created=len(created_short),
            long_term_created=len(created_long),
            notes_json={
                "skipped_short_term": skipped_short[:6],
                "skipped_long_term": skipped_long[:6],
                "memory_namespace": memory_namespace or "",
            },
        )
        db.add(event)
        db.commit()

        for memory in [*created_short, *created_long, event]:
            db.refresh(memory)

        return {
            "session_id": session_id,
            "memory_namespace": memory_namespace or None,
            "trace_id": trace_id,
            "source": source,
            "short_term_created": [self._serialize_short_term(memory) for memory in created_short],
            "long_term_created": created_long,
            "skipped_short_term": skipped_short,
            "skipped_long_term": skipped_long,
            "event": event,
        }

    def _create_short_term_if_new(
        self,
        db: Session,
        wallet_address: str,
        session_id: str,
        memory_type: str,
        content: str,
        ttl,
    ) -> ShortTermMemory | None:
        existing = db.scalar(
            select(ShortTermMemory)
            .where(ShortTermMemory.owner_wallet_address == wallet_address)
            .where(ShortTermMemory.session_id == session_id)
            .where(ShortTermMemory.memory_type == memory_type)
            .where(ShortTermMemory.content == content)
            .where(or_(ShortTermMemory.ttl_or_expire_at.is_(None), ShortTermMemory.ttl_or_expire_at >= utc_now()))
        )
        if existing is not None:
            return None
        memory = ShortTermMemory(
            owner_wallet_address=wallet_address,
            session_id=session_id,
            memory_type=memory_type,
            content=content,
            ttl_or_expire_at=ttl,
        )
        db.add(memory)
        db.flush()
        return memory

    def _create_long_term_if_new(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int | None,
        category: str,
        content: str,
        source: str,
        score: int,
    ) -> LongTermMemory | None:
        stmt = (
            select(LongTermMemory)
            .where(LongTermMemory.owner_wallet_address == wallet_address)
            .where(LongTermMemory.category == category)
            .where(LongTermMemory.content == content)
        )
        if kb_id is None:
            stmt = stmt.where(LongTermMemory.kb_id.is_(None))
        else:
            stmt = stmt.where(LongTermMemory.kb_id == kb_id)
        existing = db.scalar(stmt)
        if existing is not None:
            return None
        memory = LongTermMemory(
            owner_wallet_address=wallet_address,
            kb_id=kb_id,
            category=category,
            content=content,
            source=source,
            score=score,
        )
        db.add(memory)
        db.flush()
        return memory

    def _extract_long_term_candidates(self, query: str, answer: str) -> list[str]:
        text = "\n".join(item for item in [query, answer] if item)
        results: list[str] = []
        for pattern in PREFERENCE_PATTERNS:
            for match in pattern.finditer(text):
                candidate = self._clean(match.group(1))
                if len(candidate) < 2:
                    continue
                if not candidate.startswith("用户偏好："):
                    candidate = f"用户偏好：{candidate}"
                results.append(candidate)
        return results

    def _compose_recent_turn(self, query: str, answer: str, source_refs: list[str]) -> str:
        parts = [f"用户问题：{query}"]
        if answer:
            parts.append(f"助手回答：{self._truncate(answer, self.settings.auto_memory_recent_turn_max_chars)}")
        if source_refs:
            parts.append(f"引用知识源：{', '.join(source_refs[:3])}")
        return "\n".join(parts)

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        value = value.strip()
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def _clean(value: str | None) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _dedupe_items(self, values: list[str], limit: int) -> list[str]:
        items: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = self._clean(value)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            items.append(cleaned)
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _serialize_short_term(memory: ShortTermMemory) -> dict:
        memory_namespace, session_id = parse_memory_session_key(memory.session_id)
        return {
            "id": memory.id,
            "owner_wallet_address": memory.owner_wallet_address,
            "session_id": session_id or "",
            "memory_namespace": memory_namespace,
            "memory_type": memory.memory_type,
            "content": memory.content,
            "ttl_or_expire_at": memory.ttl_or_expire_at,
            "created_at": memory.created_at,
        }
