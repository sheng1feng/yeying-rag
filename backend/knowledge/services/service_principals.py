from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.models import ServicePrincipal, WalletUser
from knowledge.models.entities import SERVICE_PRINCIPAL_STATUSES


class ServicePrincipalService:
    SUPPORTED_IDENTITY_TYPES = {"api_key"}

    def create_principal(
        self,
        db: Session,
        owner_wallet_address: str,
        *,
        service_id: str,
        display_name: str,
        identity_type: str = "api_key",
    ) -> tuple[ServicePrincipal, str]:
        self._get_owner_or_404(db, owner_wallet_address)
        normalized_identity_type = self._normalize_identity_type(identity_type)
        normalized_service_id = str(service_id or "").strip()
        normalized_display_name = str(display_name or "").strip()
        if not normalized_service_id:
            raise ValueError("service_id cannot be empty")
        if not normalized_display_name:
            raise ValueError("display_name cannot be empty")
        existing = db.scalar(
            select(ServicePrincipal)
            .where(ServicePrincipal.owner_wallet_address == owner_wallet_address)
            .where(ServicePrincipal.service_id == normalized_service_id)
        )
        if existing is not None:
            raise ValueError("service_id already exists for owner")
        raw_api_key = self._generate_api_key()
        principal = ServicePrincipal(
            owner_wallet_address=owner_wallet_address,
            service_id=normalized_service_id,
            display_name=normalized_display_name,
            identity_type=normalized_identity_type,
            credential_fingerprint=self._fingerprint_api_key(raw_api_key),
            public_key_jwk={},
            principal_status="active",
        )
        db.add(principal)
        db.commit()
        db.refresh(principal)
        return principal, raw_api_key

    def list_principals(self, db: Session, owner_wallet_address: str) -> list[ServicePrincipal]:
        self._get_owner_or_404(db, owner_wallet_address)
        return list(
            db.scalars(
                select(ServicePrincipal)
                .where(ServicePrincipal.owner_wallet_address == owner_wallet_address)
                .order_by(ServicePrincipal.created_at.desc(), ServicePrincipal.id.desc())
            ).all()
        )

    def get_principal_or_404(self, db: Session, owner_wallet_address: str, principal_id: int) -> ServicePrincipal:
        self._get_owner_or_404(db, owner_wallet_address)
        principal = db.get(ServicePrincipal, principal_id)
        if principal is None or principal.owner_wallet_address != owner_wallet_address:
            raise LookupError("service principal not found")
        return principal

    def update_principal(
        self,
        db: Session,
        owner_wallet_address: str,
        principal_id: int,
        *,
        display_name: str | None = None,
        principal_status: str | None = None,
    ) -> ServicePrincipal:
        principal = self.get_principal_or_404(db, owner_wallet_address, principal_id)
        if display_name is not None:
            normalized_display_name = str(display_name or "").strip()
            if not normalized_display_name:
                raise ValueError("display_name cannot be empty")
            principal.display_name = normalized_display_name
        if principal_status is not None:
            normalized_status = str(principal_status or "").strip().lower()
            if normalized_status not in SERVICE_PRINCIPAL_STATUSES:
                raise ValueError(f"principal_status must be one of: {', '.join(SERVICE_PRINCIPAL_STATUSES)}")
            principal.principal_status = normalized_status
        db.commit()
        db.refresh(principal)
        return principal

    def verify_api_key(self, db: Session, raw_api_key: str) -> ServicePrincipal:
        fingerprint = self._fingerprint_api_key(raw_api_key)
        principal = db.scalar(
            select(ServicePrincipal)
            .where(ServicePrincipal.identity_type == "api_key")
            .where(ServicePrincipal.credential_fingerprint == fingerprint)
        )
        if principal is None:
            raise LookupError("service principal not found")
        if principal.principal_status != "active":
            raise ValueError(f"service principal is {principal.principal_status}")
        return principal

    @staticmethod
    def _generate_api_key() -> str:
        return "svc_" + secrets.token_urlsafe(32)

    @staticmethod
    def _fingerprint_api_key(raw_api_key: str) -> str:
        normalized = str(raw_api_key or "").strip()
        if not normalized:
            raise ValueError("api_key cannot be empty")
        return hashlib.sha256(f"api_key:{normalized}".encode("utf-8")).hexdigest()

    def _normalize_identity_type(self, identity_type: str) -> str:
        normalized = str(identity_type or "").strip().lower()
        if normalized not in self.SUPPORTED_IDENTITY_TYPES:
            raise ValueError(f"identity_type must be one of: {', '.join(sorted(self.SUPPORTED_IDENTITY_TYPES))}")
        return normalized

    @staticmethod
    def _get_owner_or_404(db: Session, owner_wallet_address: str) -> WalletUser:
        owner = db.get(WalletUser, owner_wallet_address)
        if owner is None:
            raise LookupError("wallet user not found")
        return owner
