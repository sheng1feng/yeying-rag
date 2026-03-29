from __future__ import annotations

from datetime import datetime
from typing import Literal
from typing import Optional

from pydantic import BaseModel


class WarehouseCredentialCreateRequest(BaseModel):
    key_id: str
    key_secret: str
    root_path: str


class WarehouseBootstrapChallengeResponse(BaseModel):
    wallet_address: str
    challenge: str
    nonce: str | None = None
    issued_at: int | None = None
    expires_at: int | None = None


class WarehouseBootstrapInitializeRequest(BaseModel):
    mode: Literal["uploads_bundle", "app_root_write"] = "uploads_bundle"
    signature: str


class WarehouseBootstrapCleanupRequest(BaseModel):
    signature: str


class WarehouseBootstrapInitializeResponse(BaseModel):
    attempt_id: int
    status: Literal["succeeded", "partial_success", "failed"]
    stage: str
    mode: Literal["uploads_bundle", "app_root_write"]
    mode_label: str
    wallet_address: str
    target_path: str
    write_key_id: str | None = None
    read_key_id: str | None = None
    write_credential: Optional["WarehouseCredentialSummary"] = None
    read_credential: Optional["WarehouseCredentialSummary"] = None
    error_message: str | None = None
    warnings: list[str] = []
    cleanup_status: str | None = None


class WarehouseProvisioningAttemptRead(BaseModel):
    id: int
    mode: Literal["uploads_bundle", "app_root_write"]
    target_path: str
    status: str
    stage: str
    write_key_id: str | None = None
    read_key_id: str | None = None
    write_credential_id: int | None = None
    read_credential_id: int | None = None
    error_message: str | None = None
    details_json: dict = {}
    warnings: list[str] = []
    cleanup_status: str | None = None
    created_at: datetime
    updated_at: datetime


class WarehouseCredentialSummary(BaseModel):
    id: int
    credential_kind: Literal["read", "read_write"]
    key_id: str
    key_secret_masked: str
    root_path: str
    status: str
    last_verified_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WarehouseCredentialRevealResponse(BaseModel):
    id: int
    credential_kind: Literal["read", "read_write"]
    key_id: str
    key_secret: str


class WarehouseWriteCredentialResponse(BaseModel):
    configured: bool
    credential: WarehouseCredentialSummary | None = None


class WarehouseStatusResponse(BaseModel):
    wallet_address: str
    credentials_ready: bool = False
    read_credentials_count: int = 0
    write_credential_id: int | None = None
    write_credential_status: str | None = None
    write_root_path: str | None = None
    current_app_id: str | None = None
    current_app_root: str | None = None
    current_app_upload_dir: str | None = None
    warehouse_base_url: str | None = None


class WarehouseEntry(BaseModel):
    path: str
    name: str
    entry_type: Literal["file", "directory"]
    size: int = 0
    modified_at: datetime | None = None


class WarehouseBrowseResponse(BaseModel):
    wallet_address: str
    path: str
    credential_id: int | None = None
    credential_kind: str | None = None
    entries: list[WarehouseEntry]


class SourceBindingCreateRequest(BaseModel):
    source_path: str
    scope_type: Literal["auto", "file", "directory"] = "auto"
    credential_id: int | None = None


class SourceBindingUpdateRequest(BaseModel):
    enabled: bool


class SourceBindingResponse(BaseModel):
    id: int
    kb_id: int
    source_type: str
    source_path: str
    scope_type: str
    credential_id: int | None = None
    credential_kind: str | None = None
    credential_key_id: str | None = None
    credential_key_secret_masked: str | None = None
    credential_root_path: str | None = None
    credential_status: str | None = None
    enabled: bool
    last_imported_at: datetime | None = None
    sync_status: str | None = None
    status_reason: str | None = None
    document_count: int = 0
    chunk_count: int = 0
    last_document_indexed_at: datetime | None = None
    latest_task_id: int | None = None
    latest_task_status: str | None = None
    latest_task_finished_at: datetime | None = None
    active_task_count: int = 0

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    warehouse_path: str
    file_name: str
    size: int
    uploaded_at: datetime
    can_import: bool = True
