from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from knowledge.schemas.future_domain import (
    KnowledgeItemCandidateRead,
    KnowledgeItemEvidenceLinkRead,
    KnowledgeItemRead,
    KnowledgeItemRevisionRead,
)


class CandidateGenerationResponse(BaseModel):
    kb_id: int
    source_id: int | None = None
    asset_id: int | None = None
    created_count: int = 0
    reused_count: int = 0
    candidates: list[KnowledgeItemCandidateRead] = Field(default_factory=list)


class CandidateAcceptRequest(BaseModel):
    title: str | None = None
    statement: str | None = None
    item_type: str | None = None
    structured_payload_json: dict | None = None
    item_contract_version: str | None = None
    evidence_unit_ids: list[int] = Field(default_factory=list)
    source_note: str = ""
    applicability_scope_json: dict = Field(default_factory=dict)
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class CandidateRejectRequest(BaseModel):
    source_note: str = ""


class ManualItemCreateRequest(BaseModel):
    title: str
    statement: str
    item_type: str
    structured_payload_json: dict = Field(default_factory=dict)
    item_contract_version: str = "v1"
    evidence_unit_ids: list[int] = Field(default_factory=list)
    source_note: str = ""
    applicability_scope_json: dict = Field(default_factory=dict)
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class ManualItemUpdateRequest(BaseModel):
    title: str | None = None
    statement: str | None = None
    item_type: str | None = None
    structured_payload_json: dict | None = None
    item_contract_version: str | None = None
    evidence_unit_ids: list[int] | None = None
    source_note: str | None = None
    applicability_scope_json: dict | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class KnowledgeItemRevisionDetailRead(KnowledgeItemRevisionRead):
    evidence_links: list[KnowledgeItemEvidenceLinkRead] = Field(default_factory=list)


class KnowledgeItemDetailResponse(BaseModel):
    item: KnowledgeItemRead
    current_revision: KnowledgeItemRevisionDetailRead | None = None
    revisions: list[KnowledgeItemRevisionDetailRead] = Field(default_factory=list)
