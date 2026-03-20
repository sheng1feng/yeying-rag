from datetime import datetime

from pydantic import BaseModel, Field


class WarehouseAuthChallengeRequest(BaseModel):
    wallet_address: str


class WarehouseAuthChallengeResponse(BaseModel):
    wallet_address: str
    challenge: str
    nonce: str
    issued_at: int
    expires_at: int


class WarehouseAuthVerifyRequest(BaseModel):
    wallet_address: str
    signature: str


class WarehouseBindingStatusResponse(BaseModel):
    wallet_address: str
    bound: bool
    app_bound: bool = False
    binding_type: str | None = None
    jwt_bound: bool = False
    ucan_bound: bool = False
    current_app_id: str | None = None
    current_app_root: str | None = None
    current_app_upload_dir: str | None = None
    app_ucan_apps: list[str] = Field(default_factory=list)
    warehouse_base_url: str | None = None
    access_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    ucan_expires_at: datetime | None = None


class WarehouseUcanBootstrapRequest(BaseModel):
    wallet_address: str


class WarehouseUcanBootstrapResponse(BaseModel):
    wallet_address: str
    nonce: str
    message: str
    audience: str
    capability: list[dict]
    root_expires_at: datetime
    expires_at: datetime


class WarehouseUcanVerifyRequest(BaseModel):
    wallet_address: str
    nonce: str
    signature: str


class WarehouseAppUcanBootstrapRequest(BaseModel):
    wallet_address: str
    app_id: str
    action: str = "read,write"


class WarehouseAppUcanVerifyRequest(BaseModel):
    wallet_address: str
    app_id: str
    nonce: str
    signature: str
