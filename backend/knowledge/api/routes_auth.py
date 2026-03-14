from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from knowledge.db.session import get_db
from knowledge.schemas.auth import ChallengeRequest, ChallengeResponse, RefreshRequest, TokenResponse, VerifyRequest
from knowledge.services.auth import AuthService


router = APIRouter(prefix="/auth", tags=["auth"])
service = AuthService()


@router.post("/challenge", response_model=ChallengeResponse)
def create_challenge(payload: ChallengeRequest, db: Session = Depends(get_db)) -> ChallengeResponse:
    challenge = service.create_challenge(db, payload.wallet_address)
    return ChallengeResponse(
        wallet_address=challenge.wallet_address,
        nonce=challenge.nonce,
        message=challenge.message,
        expires_at=challenge.expires_at,
    )


@router.post("/verify", response_model=TokenResponse)
def verify_signature(payload: VerifyRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        user = service.verify_signature(db, payload.wallet_address, payload.signature)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    access, refresh = service.create_token_pair(user.wallet_address)
    return TokenResponse(
        access_token=access[0],
        refresh_token=refresh[0],
        wallet_address=user.wallet_address,
        expires_at=access[1],
        refresh_expires_at=refresh[1],
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(payload: RefreshRequest) -> TokenResponse:
    try:
        claims = service.parse_token(payload.refresh_token, expected_type="refresh")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    access, refresh = service.create_token_pair(str(claims["wallet_address"]).lower())
    return TokenResponse(
        access_token=access[0],
        refresh_token=refresh[0],
        wallet_address=str(claims["wallet_address"]).lower(),
        expires_at=access[1],
        refresh_expires_at=refresh[1],
    )


@router.post("/logout")
def logout() -> dict:
    return {"ok": True}
