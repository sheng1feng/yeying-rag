from __future__ import annotations

from pydantic import BaseModel, Field

from knowledge.schemas.future_domain import KBReleaseRead
from knowledge.schemas.grants import ServiceGrantResolvedRead


RESULT_VIEWS = ("compact", "referenced", "audit")


class ServiceSearchRequest(BaseModel):
    kb_id: int
    query: str
    top_k: int = 5
    result_view: str | None = None
    availability_mode: str = "allow_all"


class ServiceSearchSourceHealthDetail(BaseModel):
    source_id: int | None = None
    asset_id: int | None = None
    asset_path: str
    availability_status: str


class ServiceSearchEvidenceSummary(BaseModel):
    evidence_id: int
    evidence_type: str
    text_excerpt: str
    content_health_status: str
    source_ref: str


class ServiceSearchHit(BaseModel):
    result_kind: str
    score: float
    content_health_status: str
    source_health_summary: str
    source_refs: list[str] = Field(default_factory=list)

    knowledge_item_id: int | None = None
    knowledge_item_revision_id: int | None = None
    title: str | None = None
    statement: str | None = None
    item_type: str | None = None
    updated_at: str | None = None

    evidence_id: int | None = None
    evidence_type: str | None = None
    text: str | None = None

    evidence_summaries: list[ServiceSearchEvidenceSummary] = Field(default_factory=list)
    source_health_details: list[ServiceSearchSourceHealthDetail] = Field(default_factory=list)
    audit_info: dict = Field(default_factory=dict)


class ServiceSearchResponse(BaseModel):
    kb_id: int
    mode: str
    result_view: str
    availability_mode: str
    release: KBReleaseRead | None = None
    grant: ServiceGrantResolvedRead | None = None
    hits: list[ServiceSearchHit] = Field(default_factory=list)
