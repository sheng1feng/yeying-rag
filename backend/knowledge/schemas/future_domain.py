from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SourceCreateRequest(BaseModel):
    source_type: str = "warehouse"
    source_path: str
    scope_type: str = "directory"
    enabled: bool = True
    missing_policy: str = "mark_missing"


class SourceRead(BaseModel):
    id: int
    kb_id: int
    source_type: str
    source_path: str
    scope_type: str
    enabled: bool
    sync_status: str
    missing_policy: str
    last_seen_at: datetime | None = None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceAssetRead(BaseModel):
    id: int
    kb_id: int
    source_id: int
    asset_path: str
    asset_name: str
    asset_type: str
    source_version: str
    availability_status: str
    last_ingested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EvidenceUnitRead(BaseModel):
    id: int
    kb_id: int
    asset_id: int
    evidence_type: str
    text: str
    metadata_json: dict = Field(default_factory=dict)
    source_locator: dict = Field(default_factory=dict)
    vector_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeItemCandidateRead(BaseModel):
    id: int
    kb_id: int
    title: str
    statement: str
    item_type: str
    structured_payload_json: dict = Field(default_factory=dict)
    item_contract_version: str
    origin_type: str
    origin_confidence: float | None = None
    review_status: str
    created_from_job_id: str
    provenance_json: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeItemRead(BaseModel):
    id: int
    kb_id: int
    item_type: str
    origin_type: str
    lifecycle_status: str
    current_revision_id: int | None = None
    is_hotfix: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeItemRevisionRead(BaseModel):
    id: int
    knowledge_item_id: int
    revision_no: int
    title: str
    statement: str
    structured_payload_json: dict = Field(default_factory=dict)
    item_contract_version: str
    review_status: str
    visibility_status: str
    created_by: str
    reviewed_by: str
    provenance_type: str
    provenance_json: dict = Field(default_factory=dict)
    source_note: str
    applicability_scope_json: dict = Field(default_factory=dict)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    is_workspace_head: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeItemEvidenceLinkRead(BaseModel):
    id: int
    knowledge_item_revision_id: int
    evidence_unit_id: int
    role: str
    rank: int
    summary: str

    model_config = {"from_attributes": True}


class KBReleaseRead(BaseModel):
    id: int
    kb_id: int
    version: str
    status: str
    release_note: str
    published_at: datetime | None = None
    created_by: str
    supersedes_release_id: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KBReleaseItemRead(BaseModel):
    id: int
    release_id: int
    knowledge_item_id: int
    knowledge_item_revision_id: int
    item_version_hash: str
    content_health_status: str

    model_config = {"from_attributes": True}


class ServicePrincipalRead(BaseModel):
    id: int
    owner_wallet_address: str
    service_id: str
    display_name: str
    identity_type: str
    credential_fingerprint: str
    public_key_jwk: dict = Field(default_factory=dict)
    principal_status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ServiceGrantRead(BaseModel):
    id: int
    owner_wallet_address: str
    kb_id: int
    service_principal_id: int
    grant_status: str
    release_selection_mode: str
    pinned_release_id: int | None = None
    default_result_mode: str
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_by: str
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RetrievalLogRead(BaseModel):
    id: int
    owner_wallet_address: str
    kb_id: int | None = None
    service_grant_id: int | None = None
    service_principal_id: int | None = None
    query: str
    query_mode: str
    release_id: int | None = None
    result_summary_json: dict = Field(default_factory=dict)
    trace_json: dict = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}
