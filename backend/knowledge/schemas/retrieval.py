from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RetrievalFilters(BaseModel):
    source_paths: list[str] = Field(default_factory=list)
    source_kinds: list[str] = Field(default_factory=list)
    document_ids: list[int] = Field(default_factory=list)


class RetrievalPolicy(BaseModel):
    top_k: int | None = None
    memory_top_k: int | None = None
    token_budget: int | None = None
    max_context_chars: int | None = None
    include_knowledge: bool = True
    include_short_term: bool = True
    include_long_term: bool = True


class ConversationContext(BaseModel):
    session_id: str | None = None
    conversation_id: str | None = None
    memory_namespace: str | None = None
    scene: str | None = None
    intent: str | None = None


class RetrievalScope(BaseModel):
    kb_ids: list[int] = Field(min_length=1)
    source_scope: list[str] = Field(default_factory=list)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)


class CallerContext(BaseModel):
    app_name: str | None = None
    request_id: str | None = None
    client_version: str | None = None


class KnowledgeSearchRequest(BaseModel):
    query: str
    kb_ids: list[int] = Field(min_length=1)
    top_k: int | None = None
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    source_scope: list[str] = Field(default_factory=list)
    debug: bool = False


class KnowledgeRetrieveRequest(BaseModel):
    query: str
    kb_ids: list[int] = Field(min_length=1)
    top_k: int | None = None
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    source_scope: list[str] = Field(default_factory=list)
    retrieval_policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    debug: bool = False


class RecallMemoryRequest(BaseModel):
    query: str
    session_id: str | None = None
    memory_namespace: str | None = None
    kb_ids: list[int] = Field(default_factory=list)
    retrieval_policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    debug: bool = False


class KnowledgeHit(BaseModel):
    chunk_id: int
    kb_id: int
    document_id: int
    source_path: str
    text: str
    score: float
    metadata: dict = Field(default_factory=dict)
    source_kind: str | None = None
    file_name: str | None = None
    chunk_index: int | None = None


class MemoryHit(BaseModel):
    id: int
    memory_kind: Literal["short_term", "long_term"]
    memory_type: str
    content: str
    score: float
    created_at: datetime
    session_id: str | None = None
    kb_id: int | None = None
    source: str | None = None
    metadata: dict = Field(default_factory=dict)


class ContextSection(BaseModel):
    section_type: Literal["short_term_memory", "long_term_memory", "knowledge"]
    title: str
    content: str
    item_count: int
    truncated: bool = False
    char_count: int
    source_refs: list[str] = Field(default_factory=list)


class AppliedRetrievalPolicy(BaseModel):
    top_k: int
    memory_top_k: int
    token_budget: int | None = None
    max_context_chars: int
    include_knowledge: bool = True
    include_short_term: bool = True
    include_long_term: bool = True


class RetrievalDebugInfo(BaseModel):
    generated_at: str
    search_filters: dict = Field(default_factory=dict)
    request_context: dict = Field(default_factory=dict)
    memory_strategy: str = ""
    notes: list[str] = Field(default_factory=list)
    result_summary: dict = Field(default_factory=dict)
    empty_reasons: list[str] = Field(default_factory=list)
    scope_effects: dict = Field(default_factory=dict)
    budget_effects: dict = Field(default_factory=dict)
    provider: dict = Field(default_factory=dict)


class KnowledgeSearchResponse(BaseModel):
    query: str
    kb_ids: list[int]
    hits: list[KnowledgeHit]
    trace_id: str
    debug: RetrievalDebugInfo | None = None


class KnowledgeRetrieveResponse(BaseModel):
    query: str
    kb_ids: list[int]
    knowledge_hits: list[KnowledgeHit]
    source_refs: list[str]
    applied_policy: AppliedRetrievalPolicy
    trace_id: str
    debug: RetrievalDebugInfo | None = None


class MemoryRecallResponse(BaseModel):
    query: str
    session_id: str | None = None
    kb_ids: list[int]
    short_term_hits: list[MemoryHit]
    long_term_hits: list[MemoryHit]
    applied_policy: AppliedRetrievalPolicy
    trace_id: str
    debug: RetrievalDebugInfo | None = None


class ContextAssembleRequest(BaseModel):
    query: str
    request_context: dict = Field(default_factory=dict)
    knowledge_hits: list[KnowledgeHit] = Field(default_factory=list)
    short_term_hits: list[MemoryHit] = Field(default_factory=list)
    long_term_hits: list[MemoryHit] = Field(default_factory=list)
    retrieval_policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    debug: bool = False


class ContextAssembleResponse(BaseModel):
    query: str
    request_context: dict = Field(default_factory=dict)
    context_sections: list[ContextSection]
    assembled_context: str
    source_refs: list[str]
    applied_policy: AppliedRetrievalPolicy
    trace_id: str
    debug: RetrievalDebugInfo | None = None


class RetrievalKnowledgeResult(BaseModel):
    hits: list[KnowledgeHit]
    source_refs: list[str]


class RetrievalMemoryResult(BaseModel):
    short_term_hits: list[MemoryHit]
    long_term_hits: list[MemoryHit]


class RetrievalContextResult(BaseModel):
    sections: list[ContextSection]
    assembled_context: str


class RetrievalTrace(BaseModel):
    trace_id: str
    applied_policy: AppliedRetrievalPolicy
    applied_scope: dict = Field(default_factory=dict)


class RetrievalContextRequest(BaseModel):
    query: str
    conversation: ConversationContext = Field(default_factory=ConversationContext)
    scope: RetrievalScope
    policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    caller: CallerContext = Field(default_factory=CallerContext)
    debug: bool = False


class RetrievalContextResponse(BaseModel):
    query: str
    conversation: ConversationContext
    scope: RetrievalScope
    caller: CallerContext
    knowledge: RetrievalKnowledgeResult
    memory: RetrievalMemoryResult
    context: RetrievalContextResult
    trace: RetrievalTrace
    debug: RetrievalDebugInfo | None = None


class BotRetrievalContextRequest(BaseModel):
    query: str
    kb_ids: list[int] = Field(min_length=1)
    user_identity: str | None = None
    session_id: str | None = None
    memory_namespace: str | None = None
    conversation_id: str | None = None
    bot_id: str | None = None
    app_id: str | None = None
    intent: str | None = None
    scene: str | None = None
    top_k: int | None = None
    token_budget: int | None = None
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    source_scope: list[str] = Field(default_factory=list)
    retrieval_policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    debug: bool = False


class BotRetrievalContextResponse(BaseModel):
    query: str
    kb_ids: list[int]
    request_context: dict = Field(default_factory=dict)
    knowledge_hits: list[KnowledgeHit]
    short_term_memory_hits: list[MemoryHit]
    long_term_memory_hits: list[MemoryHit]
    context_sections: list[ContextSection]
    assembled_context: str
    source_refs: list[str]
    applied_policy: AppliedRetrievalPolicy
    trace_id: str
    debug: RetrievalDebugInfo | None = None
