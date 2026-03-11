from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.models import KnowledgeBase
from knowledge.services.embedding import build_embedding_provider
from knowledge.services.memory import MemoryService
from knowledge.services.vector_store import build_vector_store
from knowledge.utils.time import utc_isoformat


class RetrievalService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedding_provider = build_embedding_provider()
        self.vector_store = build_vector_store()
        self.memory_service = MemoryService()

    def search(self, db: Session, wallet_address: str, kb_ids: list[int], query: str, top_k: int | None = None) -> list[dict]:
        kb_configs = self._list_kbs(db, wallet_address, kb_ids)
        top_k = self._resolve_config_int(kb_configs, "retrieval_top_k", top_k, self.settings.retrieval_top_k)
        vector = self.embedding_provider.embed_query(query)
        return self.vector_store.search(db=db, wallet_address=wallet_address, kb_ids=kb_ids, query_vector=vector, top_k=top_k, query_text=query)

    def build_retrieval_context(
        self,
        db: Session,
        wallet_address: str,
        session_id: str,
        kb_ids: list[int],
        query: str,
        top_k: int | None = None,
    ) -> dict:
        kb_configs = self._list_kbs(db, wallet_address, kb_ids)
        memory_top_k = self._resolve_config_int(kb_configs, "memory_top_k", None, self.settings.memory_top_k)
        short_term = self.memory_service.list_short_term(db, wallet_address, session_id=session_id)[:memory_top_k]
        long_term = self.memory_service.list_long_term(db, wallet_address)[:memory_top_k]
        kb_blocks = self.search(db, wallet_address, kb_ids, query, top_k=top_k)
        trace_id = str(uuid.uuid4())
        return {
            "short_term_memory_blocks": [
                {
                    "id": memory.id,
                    "memory_type": memory.memory_type,
                    "content": memory.content,
                    "created_at": memory.created_at,
                }
                for memory in short_term
            ],
            "long_term_memory_blocks": [
                {
                    "id": memory.id,
                    "memory_type": memory.category,
                    "content": memory.content,
                    "created_at": memory.created_at,
                }
                for memory in long_term
            ],
            "kb_blocks": kb_blocks,
            "source_refs": sorted({block["source_path"] for block in kb_blocks}),
            "scores": {
                "memory_count": len(short_term) + len(long_term),
                "kb_count": len(kb_blocks),
                "generated_at": utc_isoformat(),
            },
            "trace_id": trace_id,
        }

    def _list_kbs(self, db: Session, wallet_address: str, kb_ids: list[int]) -> list[KnowledgeBase]:
        normalized_ids = list(dict.fromkeys(kb_ids))
        if not normalized_ids:
            return []
        kb_rows = list(
            db.scalars(
                select(KnowledgeBase)
                .where(KnowledgeBase.owner_wallet_address == wallet_address)
                .where(KnowledgeBase.id.in_(normalized_ids))
            ).all()
        )
        if len(kb_rows) != len(normalized_ids):
            found = {row.id for row in kb_rows}
            missing = ", ".join(str(kb_id) for kb_id in normalized_ids if kb_id not in found)
            raise ValueError(f"knowledge base not found: {missing}")
        return kb_rows

    @staticmethod
    def _resolve_config_int(kbs: list[KnowledgeBase], key: str, explicit_value: int | None, fallback: int) -> int:
        if explicit_value is not None and explicit_value > 0:
            return int(explicit_value)
        values: list[int] = []
        for kb in kbs:
            raw = (kb.retrieval_config or {}).get(key)
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                values.append(value)
        return max(values) if values else fallback
