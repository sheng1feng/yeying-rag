from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from knowledge.models import KnowledgeBase
from knowledge.services.retrieval_support import (
    build_debug_info,
    decorate_knowledge_hit,
    normalize_filters,
    resolve_policy,
    resolve_config_int,
    unique_source_refs,
)


class KnowledgeRetrievalExecutor:
    def __init__(
        self,
        *,
        settings,
        embedding_provider,
        vector_store,
        list_kbs: Callable[[Session, str, list[int]], list[KnowledgeBase]],
    ) -> None:
        self.settings = settings
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.list_kbs = list_kbs

    def _provider_debug(self) -> dict:
        diagnostics = getattr(self.embedding_provider, "diagnostics", lambda: {"provider_name": "unknown"})()
        return {
            "vector_store_mode": self.settings.vector_store_mode,
            "vector_store_backend": getattr(self.vector_store, "backend_name", self.settings.vector_store_mode),
            "model_provider_mode": self.settings.model_provider_mode,
            "embedding_provider": diagnostics.get("provider_name", "unknown"),
            "embedding_provider_configured_mode": diagnostics.get("configured_mode", self.settings.model_provider_mode),
            "embedding_fallback_reason": diagnostics.get("fallback_reason", ""),
        }

    def search_hits(
        self,
        db: Session,
        wallet_address: str,
        kb_ids: list[int],
        query: str,
        top_k: int | None = None,
        filters: dict | None = None,
    ) -> list[dict]:
        kb_configs = self.list_kbs(db, wallet_address, kb_ids)
        resolved_top_k = resolve_config_int(kb_configs, "retrieval_top_k", top_k, self.settings.retrieval_top_k)
        normalized_filters = normalize_filters(filters=filters)
        vector = self.embedding_provider.embed_query(query)
        hits = self.vector_store.search(
            db=db,
            wallet_address=wallet_address,
            kb_ids=kb_ids,
            query_vector=vector,
            top_k=resolved_top_k,
            query_text=query,
            filters=normalized_filters,
        )
        return [decorate_knowledge_hit(hit) for hit in hits]

    def search_response(
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
        trace_id = trace_id or str(uuid.uuid4())
        normalized_filters = normalize_filters(filters=filters, source_scope=source_scope)
        hits = self.search_hits(
            db=db,
            wallet_address=wallet_address,
            kb_ids=kb_ids,
            query=query,
            top_k=top_k,
            filters=normalized_filters,
        )
        return {
            "query": query,
            "kb_ids": kb_ids,
            "hits": hits,
            "trace_id": trace_id,
            "debug": build_debug_info(
                enabled=debug,
                filters=normalized_filters,
                request_context=request_context,
                memory_strategy="not-used",
                notes=[f"knowledge hits={len(hits)}"],
                result_summary={
                    "knowledge_hits": len(hits),
                    "source_ref_count": len(unique_source_refs(hits)),
                    "applied_top_k": top_k if top_k is not None else None,
                },
                empty_reasons=self._empty_reasons_for_hits(hits=hits, filters=normalized_filters),
                scope_effects={
                    "knowledge": {
                        "kb_ids": kb_ids,
                        "source_scope": normalized_filters["source_paths"],
                        "filters_applied": normalized_filters,
                    }
                },
                provider=self._provider_debug(),
            ),
        }

    def retrieve(
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
        trace_id = trace_id or str(uuid.uuid4())
        kb_configs = self.list_kbs(db, wallet_address, kb_ids)
        policy = resolve_policy(
            kb_configs=kb_configs,
            settings=self.settings,
            top_k=top_k if top_k is not None else (retrieval_policy or {}).get("top_k"),
            memory_top_k=(retrieval_policy or {}).get("memory_top_k"),
            token_budget=(retrieval_policy or {}).get("token_budget"),
            max_context_chars=(retrieval_policy or {}).get("max_context_chars"),
            include_knowledge=bool((retrieval_policy or {}).get("include_knowledge", True)),
            include_short_term=bool((retrieval_policy or {}).get("include_short_term", True)),
            include_long_term=bool((retrieval_policy or {}).get("include_long_term", True)),
        )
        normalized_filters = normalize_filters(filters=filters, source_scope=source_scope)
        knowledge_hits: list[dict] = []
        if policy["include_knowledge"]:
            knowledge_hits = self.search_hits(
                db=db,
                wallet_address=wallet_address,
                kb_ids=kb_ids,
                query=query,
                top_k=policy["top_k"],
                filters=normalized_filters,
            )
        return {
            "query": query,
            "kb_ids": kb_ids,
            "knowledge_hits": knowledge_hits,
            "source_refs": unique_source_refs(knowledge_hits),
            "applied_policy": policy,
            "trace_id": trace_id,
            "debug": build_debug_info(
                enabled=debug,
                filters=normalized_filters,
                request_context=request_context,
                memory_strategy="not-used",
                notes=[f"knowledge hits={len(knowledge_hits)}", "knowledge retrieval uses vector similarity only"],
                result_summary={
                    "knowledge_hits": len(knowledge_hits),
                    "source_ref_count": len(unique_source_refs(knowledge_hits)),
                    "applied_top_k": policy["top_k"],
                },
                empty_reasons=self._empty_reasons_for_hits(hits=knowledge_hits, filters=normalized_filters),
                scope_effects={
                    "knowledge": {
                        "kb_ids": kb_ids,
                        "source_scope": normalized_filters["source_paths"],
                        "filters_applied": normalized_filters,
                    }
                },
                provider=self._provider_debug(),
            ),
        }

    @staticmethod
    def _empty_reasons_for_hits(hits: list[dict], filters: dict) -> list[str]:
        if hits:
            return []
        empty_reasons: list[str] = []
        if filters.get("source_paths"):
            empty_reasons.append("no_knowledge_hits_after_source_scope")
        if filters.get("source_kinds"):
            empty_reasons.append("no_knowledge_hits_after_source_kind_filter")
        if filters.get("document_ids"):
            empty_reasons.append("no_knowledge_hits_after_document_filter")
        if not empty_reasons:
            empty_reasons.append("no_knowledge_hits_found")
        return empty_reasons
