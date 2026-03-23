from __future__ import annotations

from pydantic import BaseModel, Field

from knowledge.schemas.future_domain import EvidenceUnitRead


class EvidenceBuildResponse(BaseModel):
    kb_id: int
    source_id: int | None = None
    asset_id: int | None = None
    processed_asset_count: int = 0
    built_evidence_count: int = 0
    skipped_asset_count: int = 0
    failed_asset_ids: list[int] = Field(default_factory=list)


class EvidenceListResponse(BaseModel):
    items: list[EvidenceUnitRead]
