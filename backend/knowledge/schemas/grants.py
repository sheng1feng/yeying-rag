from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from knowledge.schemas.future_domain import KBReleaseRead, ServiceGrantRead, ServicePrincipalRead


class ServicePrincipalCreateRequest(BaseModel):
    service_id: str
    display_name: str
    identity_type: str = "api_key"


class ServicePrincipalUpdateRequest(BaseModel):
    display_name: str | None = None
    principal_status: str | None = None


class ServicePrincipalCreateResponse(BaseModel):
    principal: ServicePrincipalRead
    api_key: str


class ServicePrincipalVerifyRequest(BaseModel):
    api_key: str


class ServicePrincipalVerifyResponse(BaseModel):
    principal: ServicePrincipalRead


class ServiceGrantCreateRequest(BaseModel):
    service_principal_id: int
    release_selection_mode: str = "latest_published"
    pinned_release_id: int | None = None
    default_result_mode: str = "compact"
    expires_at: datetime | None = None


class ServiceGrantUpdateRequest(BaseModel):
    grant_status: str | None = None
    release_selection_mode: str | None = None
    pinned_release_id: int | None = None
    default_result_mode: str | None = None
    expires_at: datetime | None = None
    revoked_by: str | None = None


class ServiceGrantResolvedRead(ServiceGrantRead):
    kb_name: str | None = None
    resolved_release: KBReleaseRead | None = None


class ServiceKnowledgeBaseRead(BaseModel):
    kb_id: int
    kb_name: str
    grant: ServiceGrantResolvedRead


class ServiceCurrentReleaseResponse(BaseModel):
    kb_id: int
    release: KBReleaseRead
    grant: ServiceGrantResolvedRead
