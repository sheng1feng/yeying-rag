from __future__ import annotations

from pydantic import BaseModel, Field

from knowledge.schemas.future_domain import KBReleaseItemRead, KBReleaseRead


class ReleasePublishRequest(BaseModel):
    version: str
    release_note: str = ""


class ReleaseHotfixRequest(BaseModel):
    version: str
    release_note: str = ""
    knowledge_item_ids: list[int] = Field(default_factory=list)
    base_release_id: int | None = None


class ReleaseRollbackRequest(BaseModel):
    version: str
    release_note: str = ""


class ReleaseDetailResponse(BaseModel):
    release: KBReleaseRead
    items: list[KBReleaseItemRead] = Field(default_factory=list)
