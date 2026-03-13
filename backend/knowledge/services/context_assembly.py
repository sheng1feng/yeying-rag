from __future__ import annotations

import uuid

from knowledge.services.retrieval_support import (
    build_debug_info,
    build_knowledge_section,
    build_memory_section,
    resolve_policy,
    unique_source_refs,
)


class ContextAssemblyExecutor:
    def __init__(self, *, settings) -> None:
        self.settings = settings

    def assemble(
        self,
        *,
        query: str,
        knowledge_hits: list[dict] | None = None,
        short_term_hits: list[dict] | None = None,
        long_term_hits: list[dict] | None = None,
        retrieval_policy: dict | None = None,
        request_context: dict | None = None,
        debug: bool = False,
        trace_id: str | None = None,
    ) -> dict:
        trace_id = trace_id or str(uuid.uuid4())
        policy = resolve_policy(
            kb_configs=[],
            settings=self.settings,
            top_k=(retrieval_policy or {}).get("top_k"),
            memory_top_k=(retrieval_policy or {}).get("memory_top_k"),
            token_budget=(retrieval_policy or {}).get("token_budget"),
            max_context_chars=(retrieval_policy or {}).get("max_context_chars"),
            include_knowledge=bool((retrieval_policy or {}).get("include_knowledge", True)),
            include_short_term=bool((retrieval_policy or {}).get("include_short_term", True)),
            include_long_term=bool((retrieval_policy or {}).get("include_long_term", True)),
        )
        knowledge_hits = list(knowledge_hits or [])
        short_term_hits = list(short_term_hits or [])
        long_term_hits = list(long_term_hits or [])

        sections: list[dict] = []
        remaining_chars = policy["max_context_chars"]

        if policy["include_short_term"] and short_term_hits:
            section = build_memory_section(
                section_type="short_term_memory",
                title="Short-Term Memory",
                hits=short_term_hits,
                remaining_chars=remaining_chars,
            )
            if section is not None:
                sections.append(section)
                remaining_chars = max(0, remaining_chars - section["char_count"] - 2)

        if policy["include_long_term"] and long_term_hits and remaining_chars > 0:
            section = build_memory_section(
                section_type="long_term_memory",
                title="Long-Term Memory",
                hits=long_term_hits,
                remaining_chars=remaining_chars,
            )
            if section is not None:
                sections.append(section)
                remaining_chars = max(0, remaining_chars - section["char_count"] - 2)

        if policy["include_knowledge"] and knowledge_hits and remaining_chars > 0:
            section = build_knowledge_section(knowledge_hits=knowledge_hits, remaining_chars=remaining_chars)
            if section is not None:
                sections.append(section)

        assembled_context = "\n\n".join(
            f"## {section['title']}\n{section['content']}"
            for section in sections
            if section["content"].strip()
        )
        truncated_sections = [section["section_type"] for section in sections if section.get("truncated")]
        section_order = [section["section_type"] for section in sections]
        return {
            "query": query,
            "request_context": request_context or {},
            "context_sections": sections,
            "assembled_context": assembled_context,
            "applied_policy": policy,
            "trace_id": trace_id,
            "debug": build_debug_info(
                enabled=debug,
                filters={},
                request_context=request_context,
                memory_strategy="context assembly uses ordered sections with char budget",
                notes=[
                    f"context sections={len(sections)}",
                    f"assembled chars={len(assembled_context)}",
                    f"source refs={len(unique_source_refs(knowledge_hits))}",
                ],
                result_summary={
                    "context_sections": len(sections),
                    "assembled_chars": len(assembled_context),
                    "source_ref_count": len(unique_source_refs(knowledge_hits)),
                },
                empty_reasons=self._empty_reasons(
                    knowledge_hits=knowledge_hits,
                    short_term_hits=short_term_hits,
                    long_term_hits=long_term_hits,
                    sections=sections,
                    policy=policy,
                ),
                scope_effects={
                    "context": {
                        "section_order": section_order,
                        "source_refs_from_knowledge_hits": unique_source_refs(knowledge_hits),
                    }
                },
                budget_effects={
                    "requested_token_budget": (retrieval_policy or {}).get("token_budget"),
                    "requested_max_context_chars": (retrieval_policy or {}).get("max_context_chars"),
                    "applied_max_context_chars": policy["max_context_chars"],
                    "assembled_chars": len(assembled_context),
                    "truncated_sections": truncated_sections,
                },
            ),
        }

    @staticmethod
    def _empty_reasons(
        *,
        knowledge_hits: list[dict],
        short_term_hits: list[dict],
        long_term_hits: list[dict],
        sections: list[dict],
        policy: dict,
    ) -> list[str]:
        empty_reasons: list[str] = []
        if not sections:
            if not policy["include_knowledge"] and not policy["include_short_term"] and not policy["include_long_term"]:
                empty_reasons.append("all_context_sources_disabled")
            elif not knowledge_hits and not short_term_hits and not long_term_hits:
                empty_reasons.append("no_context_inputs")
            else:
                empty_reasons.append("context_budget_excluded_all_content")
        return empty_reasons
