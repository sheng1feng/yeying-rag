from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.models import KnowledgeBase
from knowledge.services.context_assembly import ContextAssemblyExecutor
from knowledge.services.embedding import build_embedding_provider
from knowledge.services.knowledge_retrieval import KnowledgeRetrievalExecutor
from knowledge.services.memory import MemoryService
from knowledge.services.memory_recall import MemoryRecallExecutor
from knowledge.services.retrieval_support import merge_debug_infos, normalize_filters, unique_source_refs
from knowledge.services.vector_store import build_vector_store
from knowledge.utils.time import utc_isoformat


class RetrievalService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedding_provider = build_embedding_provider()
        self.vector_store = build_vector_store()
        self.memory_service = MemoryService()
        self.knowledge_retriever = KnowledgeRetrievalExecutor(
            settings=self.settings,
            embedding_provider=self.embedding_provider,
            vector_store=self.vector_store,
            list_kbs=self._list_kbs,
        )
        self.memory_recaller = MemoryRecallExecutor(
            settings=self.settings,
            memory_service=self.memory_service,
            list_kbs=self._list_kbs,
            list_recallable_long_term=self._list_recallable_long_term,
        )
        self.context_assembler = ContextAssemblyExecutor(settings=self.settings)

    def search(
        self,
        db: Session,
        wallet_address: str,
        kb_ids: list[int],
        query: str,
        top_k: int | None = None,
        filters: dict | None = None,
    ) -> list[dict]:
        return self.knowledge_retriever.search_hits(
            db=db,
            wallet_address=wallet_address,
            kb_ids=kb_ids,
            query=query,
            top_k=top_k,
            filters=filters,
        )

    def search_knowledge(
        self,
        db: Session,
        wallet_address: str,
        kb_ids: list[int],
        query: str,
        top_k: int | None = None,
        filters: dict | None = None,
        source_scope: list[str] | None = None,
        debug: bool = False,
        request_context: dict | None = None,
        trace_id: str | None = None,
    ) -> dict:
        return self.knowledge_retriever.search_response(
            db=db,
            wallet_address=wallet_address,
            kb_ids=kb_ids,
            query=query,
            top_k=top_k,
            filters=filters,
            source_scope=source_scope,
            debug=debug,
            request_context=request_context,
            trace_id=trace_id,
        )

    def retrieve_knowledge(
        self,
        db: Session,
        wallet_address: str,
        kb_ids: list[int],
        query: str,
        top_k: int | None = None,
        filters: dict | None = None,
        source_scope: list[str] | None = None,
        retrieval_policy: dict | None = None,
        debug: bool = False,
        request_context: dict | None = None,
        trace_id: str | None = None,
    ) -> dict:
        return self.knowledge_retriever.retrieve(
            db=db,
            wallet_address=wallet_address,
            kb_ids=kb_ids,
            query=query,
            top_k=top_k,
            filters=filters,
            source_scope=source_scope,
            retrieval_policy=retrieval_policy,
            debug=debug,
            request_context=request_context,
            trace_id=trace_id,
        )

    def recall_memory(
        self,
        db: Session,
        wallet_address: str,
        query: str,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        kb_ids: list[int] | None = None,
        retrieval_policy: dict | None = None,
        debug: bool = False,
        request_context: dict | None = None,
        trace_id: str | None = None,
    ) -> dict:
        return self.memory_recaller.recall(
            db=db,
            wallet_address=wallet_address,
            query=query,
            session_id=session_id,
            memory_namespace=memory_namespace,
            kb_ids=kb_ids,
            retrieval_policy=retrieval_policy,
            debug=debug,
            request_context=request_context,
            trace_id=trace_id,
        )

    def assemble_context(
        self,
        query: str,
        knowledge_hits: list[dict] | None = None,
        short_term_hits: list[dict] | None = None,
        long_term_hits: list[dict] | None = None,
        retrieval_policy: dict | None = None,
        request_context: dict | None = None,
        debug: bool = False,
        trace_id: str | None = None,
    ) -> dict:
        assembled = self.context_assembler.assemble(
            query=query,
            knowledge_hits=knowledge_hits,
            short_term_hits=short_term_hits,
            long_term_hits=long_term_hits,
            retrieval_policy=retrieval_policy,
            request_context=request_context,
            debug=debug,
            trace_id=trace_id,
        )
        return {
            **assembled,
            "source_refs": unique_source_refs(list(knowledge_hits or [])),
        }

    def generate_context(
        self,
        db: Session,
        wallet_address: str,
        kb_ids: list[int],
        query: str,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        top_k: int | None = None,
        filters: dict | None = None,
        source_scope: list[str] | None = None,
        retrieval_policy: dict | None = None,
        request_context: dict | None = None,
        debug: bool = False,
        trace_id: str | None = None,
    ) -> dict:
        trace_id = trace_id or str(uuid.uuid4())
        request_context = request_context or {}
        normalized_filters = normalize_filters(filters=filters, source_scope=source_scope)

        retrieval = self.retrieve_knowledge(
            db=db,
            wallet_address=wallet_address,
            kb_ids=kb_ids,
            query=query,
            top_k=top_k,
            filters=normalized_filters,
            retrieval_policy=retrieval_policy,
            debug=debug,
            request_context=request_context,
            trace_id=trace_id,
        )
        memory = self.recall_memory(
            db=db,
            wallet_address=wallet_address,
            query=query,
            session_id=session_id,
            memory_namespace=memory_namespace,
            kb_ids=kb_ids,
            retrieval_policy=retrieval["applied_policy"],
            debug=debug,
            request_context=request_context,
            trace_id=trace_id,
        )
        assembled = self.context_assembler.assemble(
            query=query,
            knowledge_hits=retrieval["knowledge_hits"],
            short_term_hits=memory["short_term_hits"],
            long_term_hits=memory["long_term_hits"],
            retrieval_policy=retrieval["applied_policy"],
            request_context=request_context,
            debug=debug,
            trace_id=trace_id,
        )
        return {
            "query": query,
            "kb_ids": kb_ids,
            "request_context": request_context,
            "knowledge_hits": retrieval["knowledge_hits"],
            "short_term_memory_hits": memory["short_term_hits"],
            "long_term_memory_hits": memory["long_term_hits"],
            "context_sections": assembled["context_sections"],
            "assembled_context": assembled["assembled_context"],
            "source_refs": retrieval["source_refs"],
            "applied_policy": retrieval["applied_policy"],
            "trace_id": trace_id,
            "debug": merge_debug_infos(
                enabled=debug,
                request_context=request_context,
                filters=normalized_filters,
                parts=[retrieval.get("debug"), memory.get("debug"), assembled.get("debug")],
            ),
        }

    def generate_retrieval_context(
        self,
        db: Session,
        wallet_address: str,
        kb_ids: list[int],
        query: str,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        top_k: int | None = None,
        filters: dict | None = None,
        source_scope: list[str] | None = None,
        retrieval_policy: dict | None = None,
        request_context: dict | None = None,
        debug: bool = False,
        trace_id: str | None = None,
    ) -> dict:
        return self.generate_context(
            db=db,
            wallet_address=wallet_address,
            kb_ids=kb_ids,
            query=query,
            session_id=session_id,
            memory_namespace=memory_namespace,
            top_k=top_k,
            filters=filters,
            source_scope=source_scope,
            retrieval_policy=retrieval_policy,
            request_context=request_context,
            debug=debug,
            trace_id=trace_id,
        )

    def build_retrieval_context(
        self,
        db: Session,
        wallet_address: str,
        session_id: str,
        kb_ids: list[int],
        query: str,
        top_k: int | None = None,
    ) -> dict:
        payload = self.generate_context(
            db=db,
            wallet_address=wallet_address,
            kb_ids=kb_ids,
            query=query,
            session_id=session_id,
            top_k=top_k,
            retrieval_policy=None,
            request_context={"session_id": session_id, "compatibility_mode": "legacy_retrieval_context"},
            debug=False,
        )
        return {
            "short_term_memory_blocks": [
                {
                    "id": memory["id"],
                    "memory_type": memory["memory_type"],
                    "content": memory["content"],
                    "created_at": memory["created_at"],
                }
                for memory in payload["short_term_memory_hits"]
            ],
            "long_term_memory_blocks": [
                {
                    "id": memory["id"],
                    "memory_type": memory["memory_type"],
                    "content": memory["content"],
                    "created_at": memory["created_at"],
                }
                for memory in payload["long_term_memory_hits"]
            ],
            "kb_blocks": payload["knowledge_hits"],
            "source_refs": payload["source_refs"],
            "scores": {
                "memory_count": len(payload["short_term_memory_hits"]) + len(payload["long_term_memory_hits"]),
                "kb_count": len(payload["knowledge_hits"]),
                "generated_at": utc_isoformat(),
                "applied_policy": payload["applied_policy"],
                "section_count": len(payload["context_sections"]),
            },
            "trace_id": payload["trace_id"],
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

    def _list_recallable_long_term(self, db: Session, wallet_address: str, kb_ids: list[int]) -> list:
        long_memories = self.memory_service.list_long_term(db, wallet_address)
        if not kb_ids:
            return long_memories
        allowed_kb_ids = set(kb_ids)
        return [memory for memory in long_memories if memory.kb_id is None or memory.kb_id in allowed_kb_ids]
