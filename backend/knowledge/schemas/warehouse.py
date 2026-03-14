from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class WarehouseEntry(BaseModel):
    path: str
    name: str
    entry_type: Literal["file", "directory"]
    size: int = 0
    modified_at: datetime | None = None


class WarehouseBrowseResponse(BaseModel):
    wallet_address: str
    path: str
    entries: list[WarehouseEntry]


class SourceBindingCreateRequest(BaseModel):
    source_path: str
    scope_type: Literal["file", "directory"] = "file"


class SourceBindingUpdateRequest(BaseModel):
    enabled: bool


class SourceBindingResponse(BaseModel):
    id: int
    kb_id: int
    source_type: str
    source_path: str
    scope_type: str
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
