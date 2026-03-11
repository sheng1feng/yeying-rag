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


class SourceBindingResponse(BaseModel):
    id: int
    kb_id: int
    source_type: str
    source_path: str
    scope_type: str
    enabled: bool
    last_imported_at: datetime | None = None

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    warehouse_path: str
    file_name: str
    size: int
    uploaded_at: datetime
    can_import: bool = True
