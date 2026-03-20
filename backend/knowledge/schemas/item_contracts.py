from __future__ import annotations

from pydantic import BaseModel, Field


class ItemContractError(BaseModel):
    item_type: str
    item_contract_version: str
    error_code: str
    field: str | None = None
    message: str


class ItemContractInfo(BaseModel):
    item_type: str
    item_contract_version: str
    required_fields: list[str] = Field(default_factory=list)


class ItemContractValidationResult(BaseModel):
    item_type: str
    item_contract_version: str
    payload: dict = Field(default_factory=dict)


class ItemContractValidationFailure(BaseModel):
    item_type: str
    item_contract_version: str
    errors: list[ItemContractError] = Field(default_factory=list)
