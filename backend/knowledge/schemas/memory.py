from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class LongTermMemoryCreateRequest(BaseModel):
    kb_id: int | None = None
    category: str = "general"
    content: str
    source: str = "bot"
    score: int = 100


class LongTermMemoryUpdateRequest(BaseModel):
    category: Optional[str] = None
    content: Optional[str] = None
    source: Optional[str] = None
    score: Optional[int] = None


class LongTermMemoryResponse(BaseModel):
    id: int
    owner_wallet_address: str
    kb_id: int | None = None
    category: str
    content: str
    source: str
    score: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ShortTermMemoryCreateRequest(BaseModel):
    session_id: str
    memory_type: Literal["recent_turn", "summary", "temporary_fact", "recent_preference"]
    content: str
    ttl_or_expire_at: datetime | None = None


class ShortTermMemoryResponse(BaseModel):
    id: int
    owner_wallet_address: str
    session_id: str
    memory_type: str
    content: str
    ttl_or_expire_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MemoryIngestionRequest(BaseModel):
    session_id: str
    kb_id: int | None = None
    query: str
    answer: str = ""
    source: str = "bot"
    trace_id: str = ""
    source_refs: list[str] = Field(default_factory=list)
    long_term_candidates: list[str] = Field(default_factory=list)
    short_term_candidates: list[str] = Field(default_factory=list)
    persist_recent_turn: bool = True


class MemoryIngestionEventResponse(BaseModel):
    id: int
    owner_wallet_address: str
    session_id: str
    kb_id: int | None = None
    source: str
    status: str
    trace_id: str
    query_preview: str
    answer_preview: str
    source_refs_json: list[str]
    short_term_created: int
    long_term_created: int
    notes_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class MemoryIngestionResponse(BaseModel):
    session_id: str
    trace_id: str
    source: str
    short_term_created: list[ShortTermMemoryResponse]
    long_term_created: list[LongTermMemoryResponse]
    skipped_short_term: list[str]
    skipped_long_term: list[str]
    event: MemoryIngestionEventResponse
