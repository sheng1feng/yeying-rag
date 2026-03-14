from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from knowledge.models import KnowledgeBase
from knowledge.services.conversation import build_memory_session_key, parse_memory_session_key
from knowledge.services.retrieval_support import build_debug_info, resolve_policy, score_memory


class MemoryRecallExecutor:
    def __init__(
        self,
        *,
        settings,
        memory_service,
        list_kbs: Callable[[Session, str, list[int]], list[KnowledgeBase]],
        list_recallable_long_term: Callable[[Session, str, list[int]], list],
    ) -> None:
        self.settings = settings
        self.memory_service = memory_service
        self.list_kbs = list_kbs
        self.list_recallable_long_term = list_recallable_long_term

    def recall(
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
        trace_id = trace_id or str(uuid.uuid4())
        normalized_kb_ids = list(dict.fromkeys(kb_ids or []))
        kb_configs = self.list_kbs(db, wallet_address, normalized_kb_ids) if normalized_kb_ids else []
        policy = resolve_policy(
            kb_configs=kb_configs,
            settings=self.settings,
            top_k=(retrieval_policy or {}).get("top_k"),
            memory_top_k=(retrieval_policy or {}).get("memory_top_k"),
            token_budget=(retrieval_policy or {}).get("token_budget"),
            max_context_chars=(retrieval_policy or {}).get("max_context_chars"),
            include_knowledge=bool((retrieval_policy or {}).get("include_knowledge", True)),
            include_short_term=bool((retrieval_policy or {}).get("include_short_term", True)),
            include_long_term=bool((retrieval_policy or {}).get("include_long_term", True)),
        )

        short_term_hits: list[dict] = []
        long_term_hits: list[dict] = []
        memory_session_key = build_memory_session_key(session_id=session_id, memory_namespace=memory_namespace)
        if memory_session_key and policy["include_short_term"]:
            short_memories = self.memory_service.list_short_term(db, wallet_address, session_id=memory_session_key)
            short_term_hits = [self._serialize_short_term_hit(memory, query) for memory in short_memories]
            short_term_hits.sort(key=lambda item: (item["score"], item["created_at"]), reverse=True)
            short_term_hits = short_term_hits[: policy["memory_top_k"]]

        if policy["include_long_term"]:
            long_memories = self.list_recallable_long_term(db, wallet_address, normalized_kb_ids)
            long_term_hits = [self._serialize_long_term_hit(memory, query) for memory in long_memories]
            long_term_hits.sort(key=lambda item: (item["score"], item["created_at"]), reverse=True)
            long_term_hits = long_term_hits[: policy["memory_top_k"]]

        return {
            "query": query,
            "session_id": session_id,
            "kb_ids": normalized_kb_ids,
            "short_term_hits": short_term_hits,
            "long_term_hits": long_term_hits,
            "applied_policy": policy,
            "trace_id": trace_id,
            "debug": build_debug_info(
                enabled=debug,
                filters={},
                request_context=request_context,
                memory_strategy="lexical-overlap + recency + long-term-score",
                notes=[
                    f"short-term hits={len(short_term_hits)}",
                    f"long-term hits={len(long_term_hits)}",
                    f"memory namespace={'-' if not memory_namespace else memory_namespace}",
                    "long-term recall is scoped to global memories and requested kb_ids",
                ],
                result_summary={
                    "short_term_hits": len(short_term_hits),
                    "long_term_hits": len(long_term_hits),
                    "applied_memory_top_k": policy["memory_top_k"],
                },
                empty_reasons=self._empty_reasons(
                    session_id=session_id,
                    memory_namespace=memory_namespace,
                    short_term_hits=short_term_hits,
                    long_term_hits=long_term_hits,
                    include_short_term=policy["include_short_term"],
                    include_long_term=policy["include_long_term"],
                ),
                scope_effects={
                    "memory": {
                        "session_id": session_id,
                        "memory_namespace": memory_namespace,
                        "short_term_scope_key": memory_session_key or "",
                        "kb_ids": normalized_kb_ids,
                        "long_term_scope": "global_or_requested_kbs",
                    }
                },
            ),
        }

    def _serialize_short_term_hit(self, memory: Any, query: str) -> dict:
        memory_namespace, session_id = parse_memory_session_key(memory.session_id)
        return {
            "id": memory.id,
            "memory_kind": "short_term",
            "memory_type": memory.memory_type,
            "content": memory.content,
            "score": score_memory(query, memory.content, created_at=memory.created_at, freshness_weight=0.35),
            "created_at": memory.created_at,
            "session_id": session_id,
            "kb_id": None,
            "source": "memory_short_term",
            "metadata": {
                "ttl_or_expire_at": memory.ttl_or_expire_at.isoformat() if memory.ttl_or_expire_at else None,
                "memory_namespace": memory_namespace,
                "scope_type": "session_namespace",
            },
        }

    def _serialize_long_term_hit(self, memory: Any, query: str) -> dict:
        return {
            "id": memory.id,
            "memory_kind": "long_term",
            "memory_type": memory.category,
            "content": memory.content,
            "score": score_memory(
                query,
                memory.content,
                created_at=memory.created_at,
                freshness_weight=0.1,
                persistent_score=memory.score,
            ),
            "created_at": memory.created_at,
            "session_id": None,
            "kb_id": memory.kb_id,
            "source": memory.source,
            "metadata": {
                "category": memory.category,
                "score": memory.score,
                "scope_type": "global" if memory.kb_id is None else "kb",
            },
        }

    @staticmethod
    def _empty_reasons(
        *,
        session_id: str | None,
        memory_namespace: str | None,
        short_term_hits: list[dict],
        long_term_hits: list[dict],
        include_short_term: bool,
        include_long_term: bool,
    ) -> list[str]:
        empty_reasons: list[str] = []
        if include_short_term and not short_term_hits:
            if not session_id:
                empty_reasons.append("short_term_session_scope_missing")
            elif memory_namespace:
                empty_reasons.append("no_short_term_memories_in_namespace")
            else:
                empty_reasons.append("no_short_term_memories_in_session")
        if include_long_term and not long_term_hits:
            empty_reasons.append("no_long_term_memories_in_scope")
        return empty_reasons
