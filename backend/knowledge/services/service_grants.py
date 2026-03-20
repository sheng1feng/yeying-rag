from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.models import KBRelease, KnowledgeBase, ServiceGrant
from knowledge.models.entities import SERVICE_GRANT_RELEASE_SELECTION_MODES, SERVICE_GRANT_STATUSES
from knowledge.services.release_management import ReleaseManagementService
from knowledge.services.service_principals import ServicePrincipalService
from knowledge.utils.time import utc_now


class ServiceGrantService:
    def __init__(
        self,
        principal_service: ServicePrincipalService | None = None,
        release_management_service: ReleaseManagementService | None = None,
    ) -> None:
        self.principal_service = principal_service or ServicePrincipalService()
        self.release_management_service = release_management_service or ReleaseManagementService()

    def create_grant(
        self,
        db: Session,
        owner_wallet_address: str,
        kb_id: int,
        *,
        service_principal_id: int,
        release_selection_mode: str,
        pinned_release_id: int | None,
        default_result_mode: str,
        expires_at=None,
    ) -> ServiceGrant:
        kb = self._get_kb_or_404(db, owner_wallet_address, kb_id)
        principal = self.principal_service.get_principal_or_404(db, owner_wallet_address, service_principal_id)
        normalized_mode, resolved_pinned_release_id = self._normalize_release_selection(
            db,
            kb.id,
            release_selection_mode,
            pinned_release_id,
        )
        existing = db.scalar(
            select(ServiceGrant)
            .where(ServiceGrant.kb_id == kb.id)
            .where(ServiceGrant.service_principal_id == principal.id)
        )
        if existing is not None:
            raise ValueError("grant already exists for kb and service principal")
        grant = ServiceGrant(
            owner_wallet_address=owner_wallet_address,
            kb_id=kb.id,
            service_principal_id=principal.id,
            grant_status=self._derive_grant_status(expires_at, requested_status="active"),
            release_selection_mode=normalized_mode,
            pinned_release_id=resolved_pinned_release_id,
            default_result_mode=str(default_result_mode or "compact").strip() or "compact",
            expires_at=expires_at,
        )
        db.add(grant)
        db.commit()
        db.refresh(grant)
        return grant

    def list_grants(self, db: Session, owner_wallet_address: str, kb_id: int) -> list[ServiceGrant]:
        self._get_kb_or_404(db, owner_wallet_address, kb_id)
        grants = list(
            db.scalars(
                select(ServiceGrant)
                .where(ServiceGrant.kb_id == kb_id)
                .order_by(ServiceGrant.created_at.desc(), ServiceGrant.id.desc())
            ).all()
        )
        self._refresh_expired_grants(db, grants)
        return grants

    def get_grant_or_404(self, db: Session, owner_wallet_address: str, kb_id: int, grant_id: int) -> ServiceGrant:
        self._get_kb_or_404(db, owner_wallet_address, kb_id)
        grant = db.get(ServiceGrant, grant_id)
        if grant is None or grant.kb_id != kb_id or grant.owner_wallet_address != owner_wallet_address:
            raise LookupError("service grant not found")
        self._refresh_expired_grants(db, [grant])
        return grant

    def update_grant(
        self,
        db: Session,
        owner_wallet_address: str,
        kb_id: int,
        grant_id: int,
        *,
        grant_status: str | None = None,
        release_selection_mode: str | None = None,
        pinned_release_id: int | None = None,
        default_result_mode: str | None = None,
        expires_at=None,
        revoked_by: str | None = None,
    ) -> ServiceGrant:
        grant = self.get_grant_or_404(db, owner_wallet_address, kb_id, grant_id)
        if release_selection_mode is not None or pinned_release_id is not None:
            normalized_mode, resolved_pinned_release_id = self._normalize_release_selection(
                db,
                kb_id,
                release_selection_mode or grant.release_selection_mode,
                pinned_release_id,
            )
            grant.release_selection_mode = normalized_mode
            grant.pinned_release_id = resolved_pinned_release_id
        if default_result_mode is not None:
            grant.default_result_mode = str(default_result_mode or "").strip() or grant.default_result_mode
        if expires_at is not None:
            grant.expires_at = expires_at
        if grant_status is not None:
            normalized_status = str(grant_status or "").strip().lower()
            if normalized_status not in SERVICE_GRANT_STATUSES:
                raise ValueError(f"grant_status must be one of: {', '.join(SERVICE_GRANT_STATUSES)}")
            grant.grant_status = normalized_status
            if normalized_status == "revoked":
                grant.revoked_at = utc_now()
                grant.revoked_by = str(revoked_by or owner_wallet_address).strip()
        else:
            grant.grant_status = self._derive_grant_status(grant.expires_at, requested_status=grant.grant_status)
        db.commit()
        db.refresh(grant)
        return grant

    def list_grants_for_principal(self, db: Session, principal_id: int) -> list[ServiceGrant]:
        grants = list(
            db.scalars(
                select(ServiceGrant)
                .where(ServiceGrant.service_principal_id == principal_id)
                .order_by(ServiceGrant.created_at.desc(), ServiceGrant.id.desc())
            ).all()
        )
        self._refresh_expired_grants(db, grants)
        return grants

    def resolve_release_for_grant(self, db: Session, grant: ServiceGrant) -> KBRelease:
        self._refresh_expired_grants(db, [grant])
        if grant.grant_status != "active":
            raise ValueError(f"service grant is {grant.grant_status}")
        if grant.release_selection_mode == "pinned_release":
            if grant.pinned_release_id is None:
                raise ValueError("pinned release grant missing pinned_release_id")
            release = db.get(KBRelease, grant.pinned_release_id)
            if release is None or release.kb_id != grant.kb_id:
                raise LookupError("pinned release not found")
            return release
        release = self.release_management_service.get_current_release_or_404(db, grant.owner_wallet_address, grant.kb_id)
        return release

    def mark_grant_used(self, db: Session, grant: ServiceGrant) -> ServiceGrant:
        grant.last_used_at = utc_now()
        db.commit()
        db.refresh(grant)
        return grant

    def _normalize_release_selection(
        self,
        db: Session,
        kb_id: int,
        release_selection_mode: str,
        pinned_release_id: int | None,
    ) -> tuple[str, int | None]:
        normalized_mode = str(release_selection_mode or "").strip().lower()
        if normalized_mode not in SERVICE_GRANT_RELEASE_SELECTION_MODES:
            raise ValueError(
                f"release_selection_mode must be one of: {', '.join(SERVICE_GRANT_RELEASE_SELECTION_MODES)}"
            )
        if normalized_mode == "pinned_release":
            if pinned_release_id is None:
                raise ValueError("pinned_release_id is required when release_selection_mode is pinned_release")
            release = db.get(KBRelease, pinned_release_id)
            if release is None or release.kb_id != kb_id:
                raise ValueError("pinned_release_id not found in knowledge base")
            return normalized_mode, release.id
        return normalized_mode, None

    @staticmethod
    def _derive_grant_status(expires_at, requested_status: str) -> str:
        normalized_requested_status = str(requested_status or "active").strip().lower()
        if normalized_requested_status in {"revoked", "suspended"}:
            return normalized_requested_status
        if expires_at is not None and expires_at <= utc_now():
            return "expired"
        return "active"

    @staticmethod
    def _refresh_expired_grants(db: Session, grants: list[ServiceGrant]) -> None:
        changed = False
        now = utc_now()
        for grant in grants:
            if grant.grant_status in {"revoked", "suspended"}:
                continue
            if grant.expires_at is not None and grant.expires_at <= now and grant.grant_status != "expired":
                grant.grant_status = "expired"
                changed = True
        if changed:
            db.commit()
            for grant in grants:
                db.refresh(grant)

    @staticmethod
    def _get_kb_or_404(db: Session, owner_wallet_address: str, kb_id: int) -> KnowledgeBase:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None or kb.owner_wallet_address != owner_wallet_address:
            raise LookupError("knowledge base not found")
        return kb
