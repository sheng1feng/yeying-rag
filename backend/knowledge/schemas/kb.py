from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from knowledge.schemas.warehouse import SourceBindingResponse


class KBConfig(BaseModel):
    chunk_size: int = 800
    chunk_overlap: int = 120
    retrieval_top_k: int = 6
    memory_top_k: int = 4
    embedding_model: str = "text-embedding-3-small"


class KBCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    retrieval_config: KBConfig | None = None


class KBUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    retrieval_config: Optional[KBConfig] = None


class KBResponse(BaseModel):
    id: int
    name: str
    description: str
    status: str
    retrieval_config: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KBStatsResponse(BaseModel):
    kb_id: int
    bindings_count: int
    documents_count: int
    chunks_count: int
    latest_task_status: str | None = None
    latest_task_finished_at: datetime | None = None


class KBRecentTaskResponse(BaseModel):
    id: int
    task_type: str
    status: str
    source_paths: list[str]
    created_at: datetime
    finished_at: datetime | None = None


class KBBindingStatusCountsResponse(BaseModel):
    total: int
    enabled: int
    disabled: int
    indexed: int
    syncing: int
    failed: int
    pending_sync: int


class KBWorkbenchResponse(BaseModel):
    kb_id: int
    kb_name: str
    kb_description: str
    kb_status: str
    stats: KBStatsResponse
    binding_status_counts: KBBindingStatusCountsResponse
    bindings: list[SourceBindingResponse]
    recent_tasks: list[KBRecentTaskResponse]
