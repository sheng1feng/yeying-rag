from __future__ import annotations

import secrets
from datetime import datetime, timedelta

import jwt
from eth_account import Account
from eth_account.messages import encode_defunct
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.models import AuthChallenge, WalletUser
from knowledge.utils.time import utc_now


class AuthService:
    def __init__(self) -> None:
        self.settings = get_settings()

    @staticmethod
    def normalize_wallet(wallet_address: str) -> str:
        return wallet_address.strip().lower()

    def create_challenge(self, db: Session, wallet_address: str) -> AuthChallenge:
        wallet_address = self.normalize_wallet(wallet_address)
        nonce = secrets.token_urlsafe(24)
        expires_at = utc_now() + timedelta(seconds=self.settings.challenge_ttl_seconds)
        message = (
            "Sign this message to log in to knowledge.\n"
            f"Wallet: {wallet_address}\n"
            f"Nonce: {nonce}\n"
            f"Expires At: {expires_at.isoformat()}Z"
        )
        challenge = AuthChallenge(
            wallet_address=wallet_address,
            nonce=nonce,
            message=message,
            expires_at=expires_at,
        )
        db.add(challenge)
        db.commit()
        db.refresh(challenge)
        return challenge

    def verify_signature(self, db: Session, wallet_address: str, signature: str) -> WalletUser:
        wallet_address = self.normalize_wallet(wallet_address)
        challenge = db.scalar(
            select(AuthChallenge)
            .where(AuthChallenge.wallet_address == wallet_address)
            .where(AuthChallenge.consumed.is_(False))
            .order_by(AuthChallenge.created_at.desc())
        )
        if challenge is None:
            raise ValueError("challenge not found")
        if challenge.expires_at < utc_now():
            raise ValueError("challenge expired")

        message = encode_defunct(text=challenge.message)
        recovered = Account.recover_message(message, signature=signature)
        if self.normalize_wallet(recovered) != wallet_address:
            raise ValueError("invalid signature")

        challenge.consumed = True
        user = db.get(WalletUser, wallet_address)
        if user is None:
            user = WalletUser(wallet_address=wallet_address)
            db.add(user)
        user.last_login_at = utc_now()
        db.commit()
        db.refresh(user)
        return user

    def _build_token(self, wallet_address: str, token_type: str, minutes: int) -> tuple[str, datetime]:
        expires_at = utc_now() + timedelta(minutes=minutes)
        payload = {
            "sub": wallet_address,
            "wallet_address": wallet_address,
            "type": token_type,
            "exp": expires_at,
            "iat": utc_now(),
        }
        token = jwt.encode(payload, self.settings.jwt_secret, algorithm=self.settings.jwt_algorithm)
        return token, expires_at

    def create_token_pair(self, wallet_address: str) -> tuple[tuple[str, datetime], tuple[str, datetime]]:
        access = self._build_token(wallet_address, "access", self.settings.access_token_expire_minutes)
        refresh = self._build_token(wallet_address, "refresh", self.settings.refresh_token_expire_minutes)
        return access, refresh

    def parse_token(self, token: str, expected_type: str = "access") -> dict:
        payload = jwt.decode(token, self.settings.jwt_secret, algorithms=[self.settings.jwt_algorithm])
        if payload.get("type") != expected_type:
            raise ValueError("invalid token type")
        return payload
