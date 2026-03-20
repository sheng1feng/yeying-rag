from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from knowledge.schemas.future_domain import SourceCreateRequest, SourceRead


class SourceUpdateRequest(BaseModel):
    enabled: bool | None = None
    missing_policy: str | None = None


class SourceScanStatsResponse(BaseModel):
    total_assets: int = 0
    discovered_assets: int = 0
    available_assets: int = 0
    changed_assets: int = 0
    missing_assets: int = 0
    ignored_assets: int = 0
    scanned_at: datetime


class SourceScanResponse(BaseModel):
    source: SourceRead
    stats: SourceScanStatsResponse
