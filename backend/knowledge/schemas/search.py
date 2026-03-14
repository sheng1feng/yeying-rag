from datetime import datetime

from pydantic import BaseModel


class SearchRequest(BaseModel):
    query: str
    top_k: int | None = None


class SearchBlock(BaseModel):
    chunk_id: int
    kb_id: int
    document_id: int
    source_path: str
    text: str
    score: float
    metadata: dict


class RetrievalContextRequest(BaseModel):
    session_id: str
    query: str
    kb_ids: list[int]
    top_k: int | None = None


class MemoryBlock(BaseModel):
    id: int
    memory_type: str
    content: str
    created_at: datetime


class RetrievalContextResponse(BaseModel):
    short_term_memory_blocks: list[MemoryBlock]
    long_term_memory_blocks: list[MemoryBlock]
    kb_blocks: list[SearchBlock]
    source_refs: list[str]
    scores: dict
    trace_id: str
