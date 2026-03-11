from datetime import datetime

from pydantic import BaseModel, Field


class ChallengeRequest(BaseModel):
    wallet_address: str = Field(min_length=42, max_length=64)


class ChallengeResponse(BaseModel):
    wallet_address: str
    nonce: str
    message: str
    expires_at: datetime


class VerifyRequest(BaseModel):
    wallet_address: str
    signature: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    wallet_address: str
    expires_at: datetime
    refresh_expires_at: datetime


class RefreshRequest(BaseModel):
    refresh_token: str
