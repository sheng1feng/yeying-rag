from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from knowledge.schemas.future_domain import KBReleaseRead, RetrievalLogRead
from knowledge.schemas.service_search import ServiceSearchResponse


class SearchLabCompareRequest(BaseModel):
    query: str
    top_k: int = 5
    result_view: str = "audit"
    availability_mode: str = "allow_all"


class SearchLabCompareResponse(BaseModel):
    kb_id: int
    query: str
    current_release: KBReleaseRead | None = None
    retrieval_log_id: int | None = None
    formal_only: ServiceSearchResponse
    evidence_only: ServiceSearchResponse
    formal_first: ServiceSearchResponse


class SourceGovernanceAssetRead(BaseModel):
    asset_id: int
    source_id: int
    asset_path: str
    availability_status: str
    evidence_count: int = 0
    last_ingested_at: datetime | None = None


class SourceGovernanceSourceRead(BaseModel):
    source_id: int
    source_path: str
    sync_status: str
    affected_assets: list[SourceGovernanceAssetRead] = Field(default_factory=list)


class SourceGovernanceResponse(BaseModel):
    kb_id: int
    status_counts: dict = Field(default_factory=dict)
    sources: list[SourceGovernanceSourceRead] = Field(default_factory=list)
    assets: list[SourceGovernanceAssetRead] = Field(default_factory=list)


class RetrievalLogListResponse(BaseModel):
    items: list[RetrievalLogRead] = Field(default_factory=list)
