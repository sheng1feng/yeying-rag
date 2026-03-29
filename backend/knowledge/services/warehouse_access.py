from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.models import SourceBinding, WarehouseAccessCredential
from knowledge.services.warehouse import WarehouseGateway, WarehouseRequestAuth, build_warehouse_gateway
from knowledge.services.warehouse_scope import ensure_current_app_path, normalize_warehouse_path, warehouse_app_root
from knowledge.utils.time import utc_now


READ_CREDENTIAL_KIND = "read"
WRITE_CREDENTIAL_KIND = "read_write"
ACTIVE_CREDENTIAL_STATUS = "active"
INVALID_CREDENTIAL_STATUS = "invalid"
REVOKED_LOCAL_CREDENTIAL_STATUS = "revoked_local"
MANUAL_CREDENTIAL_SOURCE = "manual_import"
BOOTSTRAP_CREDENTIAL_SOURCE = "bootstrap"


@dataclass
class ResolvedWarehouseAccess:
    auth: WarehouseRequestAuth
    credential: WarehouseAccessCredential
    binding: SourceBinding | None = None


class WarehouseAccessService:
    def __init__(self, warehouse_gateway: WarehouseGateway | None = None) -> None:
        self.settings = get_settings()
        self.fernet = Fernet(self.settings.token_encryption_secret.encode("utf-8"))
        self.warehouse_gateway = warehouse_gateway or build_warehouse_gateway()

    def create_read_credential(
        self,
        db: Session,
        wallet_address: str,
        key_id: str,
        key_secret: str,
        root_path: str,
        *,
        commit: bool = True,
        credential_source: str = MANUAL_CREDENTIAL_SOURCE,
        upstream_access_key_id: str | None = None,
        provisioning_attempt_id: int | None = None,
        provisioning_mode: str | None = None,
        remote_name: str | None = None,
        expires_at: datetime | None = None,
    ) -> WarehouseAccessCredential:
        normalized_root = ensure_current_app_path(root_path, "root_path", self.settings)
        self._validate_key_pair(key_id, key_secret)
        probe_auth = WarehouseRequestAuth.basic(key_id, key_secret)
        exists, _ = self.path_exists_with_auth(wallet_address, normalized_root, probe_auth)
        if not exists:
            raise ValueError(f"warehouse path not accessible with credential: {normalized_root}")
        credential = self._upsert_credential(
            db,
            wallet_address=wallet_address,
            credential_kind=READ_CREDENTIAL_KIND,
            key_id=key_id,
            key_secret=key_secret,
            root_path=normalized_root,
            credential_source=credential_source,
            upstream_access_key_id=upstream_access_key_id,
            provisioning_attempt_id=provisioning_attempt_id,
            provisioning_mode=provisioning_mode,
            remote_name=remote_name,
            expires_at=expires_at,
        )
        credential.status = ACTIVE_CREDENTIAL_STATUS
        credential.last_verified_at = utc_now()
        if commit:
            db.commit()
        else:
            db.flush()
        db.refresh(credential)
        return credential

    def upsert_write_credential(
        self,
        db: Session,
        wallet_address: str,
        key_id: str,
        key_secret: str,
        root_path: str,
        *,
        commit: bool = True,
        credential_source: str = MANUAL_CREDENTIAL_SOURCE,
        upstream_access_key_id: str | None = None,
        provisioning_attempt_id: int | None = None,
        provisioning_mode: str | None = None,
        remote_name: str | None = None,
        expires_at: datetime | None = None,
    ) -> WarehouseAccessCredential:
        normalized_root = ensure_current_app_path(root_path, "root_path", self.settings)
        self._validate_key_pair(key_id, key_secret)
        probe_auth = WarehouseRequestAuth.basic(key_id, key_secret)
        probe_path = None
        last_probe_error: Exception | None = None
        for candidate in self._candidate_probe_paths(normalized_root):
            try:
                exists, _ = self.path_exists_with_auth(wallet_address, candidate, probe_auth)
            except Exception as exc:  # noqa: BLE001
                if self.is_auth_error(exc):
                    last_probe_error = exc
                    continue
                raise
            if exists:
                probe_path = candidate
                break
        if probe_path is None:
            try:
                self.warehouse_gateway.ensure_app_space(
                    wallet_address,
                    auth=probe_auth,
                    base_path=normalized_root,
                    target_path=normalized_root,
                )
            except Exception as exc:  # noqa: BLE001
                if last_probe_error is not None:
                    raise last_probe_error
                raise
        else:
            # Bootstrap the app directory tree immediately after validating the write credential
            # so the first upload does not have to create the app space lazily.
            self.warehouse_gateway.ensure_app_space(
                wallet_address,
                auth=probe_auth,
                base_path=normalized_root,
                target_path=normalized_root,
            )

        existing_items = list(
            db.scalars(
                select(WarehouseAccessCredential)
                .where(WarehouseAccessCredential.owner_wallet_address == wallet_address.lower())
                .where(WarehouseAccessCredential.credential_kind == WRITE_CREDENTIAL_KIND)
                .order_by(WarehouseAccessCredential.id.asc())
            ).all()
        )
        credential = existing_items[0] if existing_items else None
        if credential is None:
            credential = WarehouseAccessCredential(
                owner_wallet_address=wallet_address.lower(),
                credential_kind=WRITE_CREDENTIAL_KIND,
                key_id=key_id.strip(),
                encrypted_key_secret=self._encrypt(key_secret.strip()),
                root_path=normalized_root,
                credential_source=credential_source,
                upstream_access_key_id=upstream_access_key_id,
                provisioning_attempt_id=provisioning_attempt_id,
                provisioning_mode=provisioning_mode,
                remote_name=remote_name,
                expires_at=expires_at,
                status=ACTIVE_CREDENTIAL_STATUS,
            )
            db.add(credential)
        else:
            credential.key_id = key_id.strip()
            credential.encrypted_key_secret = self._encrypt(key_secret.strip())
            credential.root_path = normalized_root
            credential.credential_source = credential_source
            credential.upstream_access_key_id = upstream_access_key_id
            credential.provisioning_attempt_id = provisioning_attempt_id
            credential.provisioning_mode = provisioning_mode
            credential.remote_name = remote_name
            credential.expires_at = expires_at
            credential.status = ACTIVE_CREDENTIAL_STATUS
        credential.last_verified_at = utc_now()
        for stale in existing_items[1:]:
            db.delete(stale)
        if commit:
            db.commit()
        else:
            db.flush()
        db.refresh(credential)
        return credential

    def find_reusable_bootstrap_write_credential(
        self,
        db: Session,
        wallet_address: str,
        root_path: str,
        *,
        provisioning_mode: str,
    ) -> WarehouseAccessCredential | None:
        normalized_root = ensure_current_app_path(root_path, "root_path", self.settings)
        credential = db.scalar(
            select(WarehouseAccessCredential)
            .where(WarehouseAccessCredential.owner_wallet_address == wallet_address.lower())
            .where(WarehouseAccessCredential.credential_kind == WRITE_CREDENTIAL_KIND)
            .where(WarehouseAccessCredential.credential_source == BOOTSTRAP_CREDENTIAL_SOURCE)
            .where(WarehouseAccessCredential.provisioning_mode == provisioning_mode)
            .where(WarehouseAccessCredential.root_path == normalized_root)
            .where(WarehouseAccessCredential.status == ACTIVE_CREDENTIAL_STATUS)
            .order_by(WarehouseAccessCredential.updated_at.desc(), WarehouseAccessCredential.id.desc())
        )
        return credential if self._credential_reusable(credential) else None

    def find_reusable_bootstrap_read_credential(
        self,
        db: Session,
        wallet_address: str,
        root_path: str,
        *,
        provisioning_mode: str,
    ) -> WarehouseAccessCredential | None:
        normalized_root = ensure_current_app_path(root_path, "root_path", self.settings)
        credential = db.scalar(
            select(WarehouseAccessCredential)
            .where(WarehouseAccessCredential.owner_wallet_address == wallet_address.lower())
            .where(WarehouseAccessCredential.credential_kind == READ_CREDENTIAL_KIND)
            .where(WarehouseAccessCredential.credential_source == BOOTSTRAP_CREDENTIAL_SOURCE)
            .where(WarehouseAccessCredential.provisioning_mode == provisioning_mode)
            .where(WarehouseAccessCredential.root_path == normalized_root)
            .where(WarehouseAccessCredential.status == ACTIVE_CREDENTIAL_STATUS)
            .order_by(WarehouseAccessCredential.updated_at.desc(), WarehouseAccessCredential.id.desc())
        )
        return credential if self._credential_reusable(credential) else None

    def list_read_credentials(self, db: Session, wallet_address: str) -> list[WarehouseAccessCredential]:
        return list(
            db.scalars(
                select(WarehouseAccessCredential)
                .where(WarehouseAccessCredential.owner_wallet_address == wallet_address.lower())
                .where(WarehouseAccessCredential.credential_kind == READ_CREDENTIAL_KIND)
                .order_by(WarehouseAccessCredential.created_at.asc(), WarehouseAccessCredential.id.asc())
            ).all()
        )

    def get_read_credential(self, db: Session, wallet_address: str, credential_id: int) -> WarehouseAccessCredential:
        return self._require_credential(db, wallet_address, credential_id, READ_CREDENTIAL_KIND)

    def get_write_credential(self, db: Session, wallet_address: str) -> WarehouseAccessCredential | None:
        return db.scalar(
            select(WarehouseAccessCredential)
            .where(WarehouseAccessCredential.owner_wallet_address == wallet_address.lower())
            .where(WarehouseAccessCredential.credential_kind == WRITE_CREDENTIAL_KIND)
            .order_by(WarehouseAccessCredential.updated_at.desc(), WarehouseAccessCredential.id.desc())
        )

    def revoke_read_credential_local(self, db: Session, wallet_address: str, credential_id: int) -> WarehouseAccessCredential:
        credential = db.get(WarehouseAccessCredential, int(credential_id))
        if credential is None or credential.owner_wallet_address != wallet_address.lower():
            raise ValueError("warehouse credential not found")
        if credential.credential_kind != READ_CREDENTIAL_KIND:
            raise ValueError(f"warehouse credential must be {READ_CREDENTIAL_KIND}")
        credential.status = REVOKED_LOCAL_CREDENTIAL_STATUS
        db.commit()
        db.refresh(credential)
        return credential

    def revoke_write_credential_local(self, db: Session, wallet_address: str) -> WarehouseAccessCredential:
        credential = self.get_write_credential(db, wallet_address)
        if credential is None:
            raise ValueError("warehouse write credential not configured")
        credential.status = REVOKED_LOCAL_CREDENTIAL_STATUS
        db.commit()
        db.refresh(credential)
        return credential

    def find_best_read_credential_for_path(
        self,
        db: Session,
        wallet_address: str,
        path: str,
    ) -> WarehouseAccessCredential | None:
        normalized_path = ensure_current_app_path(path, "path", self.settings)
        credentials = list(
            db.scalars(
                select(WarehouseAccessCredential)
                .where(WarehouseAccessCredential.owner_wallet_address == wallet_address.lower())
                .where(WarehouseAccessCredential.credential_kind == READ_CREDENTIAL_KIND)
                .where(WarehouseAccessCredential.status == ACTIVE_CREDENTIAL_STATUS)
                .order_by(WarehouseAccessCredential.updated_at.desc(), WarehouseAccessCredential.id.desc())
            ).all()
        )
        matches = [credential for credential in credentials if self.credential_covers_path(credential, normalized_path)]
        if not matches:
            return None
        matches.sort(key=lambda credential: len(normalize_warehouse_path(credential.root_path)), reverse=True)
        return matches[0]

    def reveal_secret(self, db: Session, wallet_address: str, credential_id: int, credential_kind: str | None = None) -> tuple[WarehouseAccessCredential, str]:
        credential = self._require_credential(db, wallet_address, credential_id, credential_kind)
        return credential, self._decrypt(credential.encrypted_key_secret)

    def delete_read_credential(self, db: Session, wallet_address: str, credential_id: int) -> None:
        credential = self._require_credential(db, wallet_address, credential_id, READ_CREDENTIAL_KIND)
        in_use = db.scalar(select(SourceBinding.id).where(SourceBinding.credential_id == credential.id))
        if in_use is not None:
            raise ValueError("credential is still referenced by source bindings")
        db.delete(credential)
        db.commit()

    def delete_write_credential(self, db: Session, wallet_address: str) -> None:
        credential = self.get_write_credential(db, wallet_address)
        if credential is None:
            return
        db.delete(credential)
        db.commit()

    def summarize(self, credential: WarehouseAccessCredential) -> dict:
        return {
            "id": credential.id,
            "credential_kind": credential.credential_kind,
            "key_id": credential.key_id,
            "key_secret_masked": self.mask_secret(self._decrypt(credential.encrypted_key_secret)),
            "root_path": credential.root_path,
            "status": credential.status,
            "last_verified_at": credential.last_verified_at,
            "last_used_at": credential.last_used_at,
            "created_at": credential.created_at,
            "updated_at": credential.updated_at,
        }

    def credential_covers_path(self, credential: WarehouseAccessCredential, path: str) -> bool:
        credential_root = normalize_warehouse_path(credential.root_path)
        normalized_path = normalize_warehouse_path(path)
        return normalized_path == credential_root or normalized_path.startswith(f"{credential_root}/")

    def resolve_binding_read_access(self, db: Session, wallet_address: str, binding: SourceBinding) -> ResolvedWarehouseAccess:
        if binding.credential_id is None:
            raise ValueError(f"binding {binding.id} is missing credential_id")
        credential = self.get_read_credential(db, wallet_address, int(binding.credential_id))
        if not self.credential_covers_path(credential, binding.source_path):
            raise ValueError(f"binding path exceeds credential scope: {binding.source_path}")
        return self._resolved_access(credential, binding=binding)

    def resolve_explicit_access(
        self,
        db: Session,
        wallet_address: str,
        path: str,
        credential_id: int | None,
        credential_kind: str | None = None,
    ) -> ResolvedWarehouseAccess | None:
        if credential_id is None:
            return None
        credential = self._require_credential(db, wallet_address, credential_id, credential_kind)
        normalized_path = ensure_current_app_path(path, "path", self.settings)
        if not self.credential_covers_path(credential, normalized_path):
            raise ValueError(f"path exceeds credential scope: {normalized_path}")
        return self._resolved_access(credential)

    def resolve_write_access(self, db: Session, wallet_address: str, path: str) -> ResolvedWarehouseAccess:
        credential = self.get_write_credential(db, wallet_address)
        if credential is None:
            raise ValueError("warehouse write credential not configured")
        if credential.status == REVOKED_LOCAL_CREDENTIAL_STATUS:
            raise ValueError("warehouse write credential revoked locally")
        normalized_path = ensure_current_app_path(path, "path", self.settings)
        if not self.credential_covers_path(credential, normalized_path):
            raise ValueError(f"path exceeds write credential scope: {normalized_path}")
        return self._resolved_access(credential)

    def resolve_browse_access(
        self,
        db: Session,
        wallet_address: str,
        path: str,
        *,
        credential_id: int | None = None,
        use_write_credential: bool = False,
    ) -> ResolvedWarehouseAccess:
        normalized_path = ensure_current_app_path(path, "path", self.settings)
        explicit = self.resolve_explicit_access(
            db,
            wallet_address,
            normalized_path,
            credential_id,
            READ_CREDENTIAL_KIND,
        )
        if explicit is not None:
            return explicit
        if use_write_credential:
            return self.resolve_write_access(db, wallet_address, normalized_path)
        raise ValueError("warehouse browse requires credential_id or use_write_credential=true")

    def resolve_path_read_access(
        self,
        db: Session,
        wallet_address: str,
        kb_id: int,
        path: str,
        *,
        preferred_binding_ids: list[int] | None = None,
        explicit_credential_id: int | None = None,
    ) -> ResolvedWarehouseAccess:
        normalized_path = ensure_current_app_path(path, "path", self.settings)
        explicit = self.resolve_explicit_access(
            db,
            wallet_address,
            normalized_path,
            explicit_credential_id,
            READ_CREDENTIAL_KIND,
        )
        if explicit is not None:
            return explicit
        binding = self.find_best_binding_for_path(db, kb_id, normalized_path, preferred_binding_ids=preferred_binding_ids)
        if binding is not None:
            resolved = self.resolve_binding_read_access(db, wallet_address, binding)
            if not self.credential_covers_path(resolved.credential, normalized_path):
                raise ValueError(f"path exceeds binding credential scope: {normalized_path}")
            return resolved
        read_credential = self.find_best_read_credential_for_path(db, wallet_address, normalized_path)
        if read_credential is not None:
            return self._resolved_access(read_credential)
        raise ValueError(f"warehouse credential not found for path: {normalized_path}")

    def find_best_binding_for_path(
        self,
        db: Session,
        kb_id: int,
        path: str,
        *,
        preferred_binding_ids: list[int] | None = None,
    ) -> SourceBinding | None:
        normalized_path = normalize_warehouse_path(path)
        stmt = (
            select(SourceBinding)
            .where(SourceBinding.kb_id == kb_id)
            .where(SourceBinding.enabled.is_(True))
            .where(SourceBinding.credential_id.is_not(None))
        )
        if preferred_binding_ids:
            stmt = stmt.where(SourceBinding.id.in_(preferred_binding_ids))
        bindings = list(db.scalars(stmt).all())
        best_binding: SourceBinding | None = None
        best_priority: tuple[int, int] | None = None
        for binding in bindings:
            binding_path = normalize_warehouse_path(binding.source_path)
            scope_type = str(binding.scope_type or "file").strip().lower()
            priority: tuple[int, int] | None = None
            if scope_type == "directory":
                if normalized_path == binding_path:
                    priority = (2, len(binding_path))
                elif normalized_path.startswith(f"{binding_path}/"):
                    priority = (1, len(binding_path))
            elif normalized_path == binding_path:
                priority = (3, len(binding_path))
            if priority is None:
                continue
            if best_priority is None or priority > best_priority:
                best_binding = binding
                best_priority = priority
        return best_binding

    def path_exists_with_auth(
        self,
        wallet_address: str,
        path: str,
        auth: WarehouseRequestAuth,
    ) -> tuple[bool, str | None]:
        normalized_path = normalize_warehouse_path(path)
        direct_accessible = False
        try:
            entries = self.warehouse_gateway.browse(wallet_address, normalized_path, auth=auth)
            direct_accessible = True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                entries = []
            else:
                raise
        if not entries and direct_accessible and self.settings.warehouse_gateway_mode == "mock":
            return True, "directory"
        for entry in entries:
            if normalize_warehouse_path(entry.path) == normalized_path:
                return True, entry.entry_type

        parent = self._parent_path(normalized_path)
        try:
            entries = self.warehouse_gateway.browse(wallet_address, parent, auth=auth)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return False, None
            if direct_accessible:
                return True, "directory"
            raise
        for entry in entries:
            if normalize_warehouse_path(entry.path) == normalized_path:
                return True, entry.entry_type
        if parent == normalized_path:
            for entry in self.warehouse_gateway.browse(wallet_address, normalized_path, auth=auth):
                if normalize_warehouse_path(entry.path) == normalized_path:
                    return True, entry.entry_type
        if direct_accessible:
            return True, "directory"
        return False, None

    def mark_access_success(self, resolved: ResolvedWarehouseAccess) -> None:
        resolved.credential.last_used_at = utc_now()
        resolved.credential.status = ACTIVE_CREDENTIAL_STATUS

    def mark_access_invalid(self, resolved: ResolvedWarehouseAccess) -> None:
        resolved.credential.status = INVALID_CREDENTIAL_STATUS

    @staticmethod
    def is_auth_error(exc: Exception) -> bool:
        return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}

    @staticmethod
    def mask_secret(secret: str) -> str:
        text = str(secret or "")
        if len(text) <= 6:
            return "*" * len(text)
        return f"{text[:4]}{'*' * max(4, len(text) - 8)}{text[-4:]}"

    def _resolved_access(self, credential: WarehouseAccessCredential, binding: SourceBinding | None = None) -> ResolvedWarehouseAccess:
        return ResolvedWarehouseAccess(
            auth=WarehouseRequestAuth.basic(
                credential.key_id,
                self._decrypt(credential.encrypted_key_secret),
            ),
            credential=credential,
            binding=binding,
        )

    def _upsert_credential(
        self,
        db: Session,
        *,
        wallet_address: str,
            credential_kind: str,
            key_id: str,
            key_secret: str,
            root_path: str,
            credential_source: str,
            upstream_access_key_id: str | None,
            provisioning_attempt_id: int | None,
            provisioning_mode: str | None,
            remote_name: str | None,
            expires_at: datetime | None,
    ) -> WarehouseAccessCredential:
        existing = db.scalar(
            select(WarehouseAccessCredential)
            .where(WarehouseAccessCredential.owner_wallet_address == wallet_address.lower())
            .where(WarehouseAccessCredential.credential_kind == credential_kind)
            .where(WarehouseAccessCredential.key_id == key_id.strip())
            .where(WarehouseAccessCredential.root_path == root_path)
        )
        if existing is None:
            existing = WarehouseAccessCredential(
                owner_wallet_address=wallet_address.lower(),
                credential_kind=credential_kind,
                key_id=key_id.strip(),
                encrypted_key_secret=self._encrypt(key_secret.strip()),
                root_path=root_path,
                credential_source=credential_source,
                upstream_access_key_id=upstream_access_key_id,
                provisioning_attempt_id=provisioning_attempt_id,
                provisioning_mode=provisioning_mode,
                remote_name=remote_name,
                expires_at=expires_at,
                status=ACTIVE_CREDENTIAL_STATUS,
            )
            db.add(existing)
        else:
            existing.encrypted_key_secret = self._encrypt(key_secret.strip())
            existing.credential_source = credential_source
            existing.upstream_access_key_id = upstream_access_key_id
            existing.provisioning_attempt_id = provisioning_attempt_id
            existing.provisioning_mode = provisioning_mode
            existing.remote_name = remote_name
            existing.expires_at = expires_at
            existing.status = ACTIVE_CREDENTIAL_STATUS
        return existing

    def _require_credential(
        self,
        db: Session,
        wallet_address: str,
        credential_id: int,
        credential_kind: str | None,
    ) -> WarehouseAccessCredential:
        credential = db.get(WarehouseAccessCredential, int(credential_id))
        if credential is None or credential.owner_wallet_address != wallet_address.lower():
            raise ValueError("warehouse credential not found")
        if credential_kind is not None and credential.credential_kind != credential_kind:
            raise ValueError(f"warehouse credential must be {credential_kind}")
        if credential.status == REVOKED_LOCAL_CREDENTIAL_STATUS:
            raise ValueError("warehouse credential revoked locally")
        return credential

    @staticmethod
    def _validate_key_pair(key_id: str, key_secret: str) -> None:
        key_id = str(key_id or "").strip()
        key_secret = str(key_secret or "").strip()
        if not key_id or not key_secret:
            raise ValueError("warehouse key_id and key_secret are required")
        if not key_id.startswith("ak_"):
            raise ValueError("warehouse key_id must start with ak_")
        if not key_secret.startswith("sk_"):
            raise ValueError("warehouse key_secret must start with sk_")

    def _encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def _decrypt(self, value: str) -> str:
        return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")

    def _candidate_probe_paths(self, root_path: str) -> list[str]:
        normalized = normalize_warehouse_path(root_path)
        current = PurePosixPath(normalized)
        app_root = PurePosixPath(warehouse_app_root(self.settings))
        candidates = [normalize_warehouse_path(str(current))]
        while current != app_root and current != current.parent:
            current = current.parent
            normalized_candidate = normalize_warehouse_path(str(current))
            if normalized_candidate.startswith(f"{app_root}/") or normalized_candidate == str(app_root):
                candidates.append(normalized_candidate)
        return candidates or [warehouse_app_root(self.settings)]

    @staticmethod
    def _parent_path(path: str) -> str:
        normalized_path = normalize_warehouse_path(path)
        parent = str(PurePosixPath(normalized_path).parent)
        if parent == ".":
            return normalized_path
        return normalize_warehouse_path(parent)

    @staticmethod
    def _credential_reusable(credential: WarehouseAccessCredential | None) -> bool:
        if credential is None:
            return False
        if not str(credential.upstream_access_key_id or "").strip():
            return False
        if credential.expires_at is not None and credential.expires_at <= utc_now():
            return False
        return True
