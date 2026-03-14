from __future__ import annotations

import re
from typing import Any

from knowledge.core.settings import Settings
from knowledge.models import KnowledgeBase
from knowledge.utils.time import utc_isoformat, utc_now


DEFAULT_MAX_CONTEXT_CHARS = 4800
TOKEN_TO_CHAR_RATIO = 4


def resolve_policy(
    *,
    kb_configs: list[KnowledgeBase],
    settings: Settings,
    top_k: int | None,
    memory_top_k: int | None,
    token_budget: int | None,
    max_context_chars: int | None,
    include_knowledge: bool,
    include_short_term: bool,
    include_long_term: bool,
) -> dict:
    resolved_top_k = resolve_config_int(kb_configs, "retrieval_top_k", top_k, settings.retrieval_top_k)
    resolved_memory_top_k = resolve_config_int(kb_configs, "memory_top_k", memory_top_k, settings.memory_top_k)
    resolved_token_budget = int(token_budget) if token_budget is not None and int(token_budget) > 0 else None
    if max_context_chars is not None and int(max_context_chars) > 0:
        resolved_max_context_chars = max(400, int(max_context_chars))
    elif resolved_token_budget is not None:
        resolved_max_context_chars = max(400, resolved_token_budget * TOKEN_TO_CHAR_RATIO)
    else:
        resolved_max_context_chars = DEFAULT_MAX_CONTEXT_CHARS
    return {
        "top_k": resolved_top_k,
        "memory_top_k": resolved_memory_top_k,
        "token_budget": resolved_token_budget,
        "max_context_chars": resolved_max_context_chars,
        "include_knowledge": include_knowledge,
        "include_short_term": include_short_term,
        "include_long_term": include_long_term,
    }


def normalize_filters(filters: dict | None = None, source_scope: list[str] | None = None) -> dict:
    filters = dict(filters or {})
    source_paths = list(dict.fromkeys([*(filters.get("source_paths") or []), *((source_scope or []))]))
    source_kinds = list(dict.fromkeys(filters.get("source_kinds") or []))
    document_ids = [int(value) for value in (filters.get("document_ids") or []) if str(value).strip()]
    return {
        "source_paths": source_paths,
        "source_kinds": source_kinds,
        "document_ids": document_ids,
    }


def build_debug_info(
    *,
    enabled: bool,
    filters: dict,
    request_context: dict | None,
    memory_strategy: str,
    notes: list[str],
    result_summary: dict | None = None,
    empty_reasons: list[str] | None = None,
    scope_effects: dict | None = None,
    budget_effects: dict | None = None,
    provider: dict | None = None,
) -> dict | None:
    if not enabled:
        return None
    return {
        "generated_at": utc_isoformat(),
        "search_filters": filters,
        "request_context": request_context or {},
        "memory_strategy": memory_strategy,
        "notes": notes,
        "result_summary": result_summary or {},
        "empty_reasons": empty_reasons or [],
        "scope_effects": scope_effects or {},
        "budget_effects": budget_effects or {},
        "provider": provider or {},
    }


def merge_debug_infos(enabled: bool, request_context: dict, filters: dict, parts: list[dict | None]) -> dict | None:
    if not enabled:
        return None
    notes: list[str] = []
    memory_strategy = ""
    result_summary: dict = {}
    scope_effects: dict = {}
    budget_effects: dict = {}
    provider: dict = {}
    empty_reasons: list[str] = []
    for part in parts:
        if not part:
            continue
        notes.extend(part.get("notes") or [])
        if part.get("memory_strategy"):
            memory_strategy = part["memory_strategy"]
        result_summary.update(part.get("result_summary") or {})
        scope_effects.update(part.get("scope_effects") or {})
        budget_effects.update(part.get("budget_effects") or {})
        provider.update(part.get("provider") or {})
        for reason in part.get("empty_reasons") or []:
            if reason not in empty_reasons:
                empty_reasons.append(reason)
    return {
        "generated_at": utc_isoformat(),
        "search_filters": filters,
        "request_context": request_context,
        "memory_strategy": memory_strategy,
        "notes": notes,
        "result_summary": result_summary,
        "empty_reasons": empty_reasons,
        "scope_effects": scope_effects,
        "budget_effects": budget_effects,
        "provider": provider,
    }


def unique_source_refs(knowledge_hits: list[dict]) -> list[str]:
    return sorted({hit["source_path"] for hit in knowledge_hits if hit.get("source_path")})


def decorate_knowledge_hit(hit: dict) -> dict:
    metadata = dict(hit.get("metadata") or {})
    return {
        **hit,
        "metadata": metadata,
        "source_kind": metadata.get("source_kind"),
        "file_name": metadata.get("file_name"),
        "chunk_index": metadata.get("chunk_index"),
    }


def score_memory(
    query: str,
    content: str,
    *,
    created_at: Any,
    freshness_weight: float,
    persistent_score: int | None = None,
) -> float:
    query_terms = set(_tokenize(query))
    content_terms = set(_tokenize(content))
    overlap = len(query_terms & content_terms) / max(1, len(query_terms)) if query_terms else 0.0
    substring_bonus = 0.4 if query.strip() and query.strip().lower() in content.lower() else 0.0
    age_hours = max(0.0, (utc_now() - created_at).total_seconds() / 3600.0) if created_at is not None else 0.0
    recency_bonus = freshness_weight * max(0.0, 1.0 - min(age_hours, 168.0) / 168.0)
    persistent_bonus = 0.0
    if persistent_score is not None:
        persistent_bonus = max(0.0, min(float(persistent_score) / 100.0, 1.0)) * 0.3
    return round(overlap + substring_bonus + recency_bonus + persistent_bonus, 6)


def build_memory_section(section_type: str, title: str, hits: list[dict], remaining_chars: int) -> dict | None:
    lines: list[str] = []
    used_chars = 0
    included = 0
    truncated = False
    for index, hit in enumerate(hits, start=1):
        line = f"{index}. ({hit['memory_type']}, score={hit['score']:.3f}) {hit['content']}"
        next_size = len(line) + (1 if lines else 0)
        if used_chars + next_size > remaining_chars:
            if not lines and remaining_chars > 12:
                shortened = line[: remaining_chars - 1].rstrip() + "…"
                lines.append(shortened)
                used_chars += len(shortened)
                included += 1
            truncated = True
            break
        lines.append(line)
        used_chars += next_size
        included += 1
    if not lines:
        return None
    content = "\n".join(lines)
    return {
        "section_type": section_type,
        "title": title,
        "content": content,
        "item_count": included,
        "truncated": truncated or included < len(hits),
        "char_count": len(content),
        "source_refs": [],
    }


def build_knowledge_section(knowledge_hits: list[dict], remaining_chars: int) -> dict | None:
    blocks: list[str] = []
    used_chars = 0
    included = 0
    truncated = False
    included_source_refs: list[str] = []
    for index, hit in enumerate(knowledge_hits, start=1):
        label = hit.get("file_name") or hit.get("source_path") or f"chunk-{hit.get('chunk_id')}"
        block = (
            f"{index}. [{label}] score={hit['score']:.3f}\n"
            f"source={hit['source_path']}\n"
            f"{hit['text']}"
        )
        next_size = len(block) + (2 if blocks else 0)
        if used_chars + next_size > remaining_chars:
            if not blocks and remaining_chars > 20:
                shortened = block[: remaining_chars - 1].rstrip() + "…"
                blocks.append(shortened)
                used_chars += len(shortened)
                included += 1
                included_source_refs.append(hit["source_path"])
            truncated = True
            break
        blocks.append(block)
        used_chars += next_size
        included += 1
        included_source_refs.append(hit["source_path"])
    if not blocks:
        return None
    content = "\n\n".join(blocks)
    return {
        "section_type": "knowledge",
        "title": "Knowledge Evidence",
        "content": content,
        "item_count": included,
        "truncated": truncated or included < len(knowledge_hits),
        "char_count": len(content),
        "source_refs": sorted(dict.fromkeys(included_source_refs)),
    }


def resolve_config_int(kbs: list[KnowledgeBase], key: str, explicit_value: int | None, fallback: int) -> int:
    if explicit_value is not None and int(explicit_value) > 0:
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


def _tokenize(value: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", value.lower())
