from __future__ import annotations

from dataclasses import dataclass
import json
from time import time
from typing import Literal

import httpx
from eth_utils import to_checksum_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.core.settings import Settings, get_settings
from knowledge.models import WarehouseAccessCredential, WarehouseProvisioningAttempt
from knowledge.services.warehouse import WarehouseGateway, WarehouseRequestAuth, build_warehouse_gateway
from knowledge.services.warehouse_access import WarehouseAccessService
from knowledge.services.warehouse_scope import warehouse_app_root, warehouse_default_upload_dir
from knowledge.utils.time import utc_now


WarehouseBootstrapMode = Literal["uploads_bundle", "app_root_write"]
WarehouseBootstrapStatus = Literal["running", "succeeded", "partial_success", "failed"]


class WarehouseBootstrapError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, url: str = "", payload: object | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.payload = payload


@dataclass
class WarehouseBootstrapPlan:
    mode: WarehouseBootstrapMode
    mode_label: str
    target_path: str
    create_read_credential: bool
    write_name: str
    write_permissions: list[str]
    write_expires_value: int
    write_expires_unit: str
    read_name: str | None = None
    read_permissions: list[str] | None = None
    read_expires_value: int | None = None
    read_expires_unit: str | None = None


class WarehouseBootstrapExecutionError(RuntimeError):
    def __init__(self, payload: dict[str, object], *, status_code: int = 400) -> None:
        message = str(payload.get("error_message") or payload.get("status") or "warehouse bootstrap failed")
        super().__init__(message)
        self.payload = payload
        self.status_code = status_code


class WarehouseBootstrapService:
    def __init__(self, *, settings: Settings | None = None, warehouse_gateway: WarehouseGateway | None = None) -> None:
        self.settings = settings or get_settings()
        self.warehouse_gateway = warehouse_gateway or build_warehouse_gateway()

    def request_challenge(self, wallet_address: str) -> dict[str, object]:
        warehouse_address = self._warehouse_wallet_address(wallet_address)
        payload = self._request_json(
            "POST",
            "/api/v1/public/auth/challenge",
            json_body={"address": warehouse_address},
        )
        data = self._extract_data(payload)
        challenge = str(data.get("challenge") or "").strip()
        if not challenge:
            raise WarehouseBootstrapError("warehouse challenge 返回缺少 challenge。")
        return {
            "wallet_address": str(data.get("address") or warehouse_address).strip(),
            "challenge": challenge,
            "nonce": str(data.get("nonce") or "").strip(),
            "issued_at": data.get("issuedAt"),
            "expires_at": data.get("expiresAt"),
        }

    def list_attempts(self, db: Session, wallet_address: str, *, limit: int = 20) -> list[WarehouseProvisioningAttempt]:
        query_limit = max(1, min(int(limit), 100))
        return list(
            db.scalars(
                select(WarehouseProvisioningAttempt)
                .where(WarehouseProvisioningAttempt.owner_wallet_address == wallet_address.lower())
                .order_by(WarehouseProvisioningAttempt.created_at.desc(), WarehouseProvisioningAttempt.id.desc())
                .limit(query_limit)
            ).all()
        )

    def get_attempt_or_404(self, db: Session, wallet_address: str, attempt_id: int) -> WarehouseProvisioningAttempt:
        attempt = db.get(WarehouseProvisioningAttempt, int(attempt_id))
        if attempt is None or attempt.owner_wallet_address != wallet_address.lower():
            raise LookupError("warehouse provisioning attempt not found")
        return attempt

    def request_cleanup(self, db: Session, wallet_address: str, attempt_id: int, *, signature: str) -> WarehouseProvisioningAttempt:
        attempt = self.get_attempt_or_404(db, wallet_address, attempt_id)
        details_json = dict(attempt.details_json or {})
        cleanup_status = self._cleanup_status_for_attempt(
            status=str(attempt.status),
            details_json=details_json,
            write_key_id=attempt.write_key_id,
            read_key_id=attempt.read_key_id,
        )
        if cleanup_status == "not_needed":
            return attempt

        token = self._verify_signature(wallet_address, signature)
        results: dict[str, str] = {}
        if attempt.write_upstream_access_key_id:
            results["write"] = self._revoke_access_key(token=token, access_key_id=str(attempt.write_upstream_access_key_id))
        if attempt.read_upstream_access_key_id:
            results["read"] = self._revoke_access_key(token=token, access_key_id=str(attempt.read_upstream_access_key_id))

        details_json["cleanup_requested"] = True
        details_json["cleanup_requested_at"] = utc_now().isoformat()
        details_json["cleanup_results"] = results
        details_json["cleanup_completed_at"] = utc_now().isoformat()
        details_json["cleanup_status"] = "cleanup_completed"
        attempt.details_json = details_json
        attempt.stage = "cleanup_completed"
        self._mark_local_credentials_revoked(db, attempt)
        db.commit()
        db.refresh(attempt)
        return attempt

    def initialize_credentials(
        self,
        db: Session,
        *,
        wallet_address: str,
        signature: str,
        mode: WarehouseBootstrapMode,
        warehouse_access_service: WarehouseAccessService,
    ) -> dict[str, object]:
        plan = self._build_plan(mode)
        attempt = self._create_attempt(db, wallet_address=wallet_address, plan=plan)
        current_stage = "started"
        write_key: dict[str, object] | None = None
        read_key: dict[str, object] | None = None
        write_credential = None
        read_credential = None

        try:
            current_stage = "verifying_signature"
            token = self._verify_signature(wallet_address, signature)

            if self.settings.warehouse_bootstrap_enable_reuse:
                current_stage = "checking_local_reuse"
                write_credential = warehouse_access_service.find_reusable_bootstrap_write_credential(
                    db,
                    wallet_address,
                    plan.target_path,
                    provisioning_mode=plan.mode,
                )
                if plan.create_read_credential:
                    read_credential = warehouse_access_service.find_reusable_bootstrap_read_credential(
                        db,
                        wallet_address,
                        plan.target_path,
                        provisioning_mode=plan.mode,
                    )
                if write_credential is not None and (not plan.create_read_credential or read_credential is not None):
                    self._apply_attempt_state(
                        attempt,
                        stage="reused_local_credentials",
                        status="succeeded",
                        write_key=self._credential_key_payload(write_credential),
                        read_key=self._credential_key_payload(read_credential),
                        write_credential_id=getattr(write_credential, "id", None),
                        read_credential_id=getattr(read_credential, "id", None),
                        error_message="",
                    )
                    db.commit()
                    db.refresh(attempt)
                    return self._build_result_payload(
                        attempt=attempt,
                        plan=plan,
                        wallet_address=wallet_address,
                        write_credential=warehouse_access_service.summarize(write_credential),
                        read_credential=warehouse_access_service.summarize(read_credential) if read_credential is not None else None,
                    )

            current_stage = "creating_write_key"
            write_key = self._create_access_key(
                token=token,
                name=plan.write_name,
                permissions=plan.write_permissions,
                expires_value=plan.write_expires_value,
                expires_unit=plan.write_expires_unit,
            )

            current_stage = "binding_write_key"
            self._bind_access_key(token=token, access_key_id=str(write_key["id"]), path=plan.target_path)

            current_stage = "ensuring_directories"
            self._ensure_directory_chain(token=token, target_path=plan.target_path)

            current_stage = "saving_write_credential"
            write_credential = warehouse_access_service.upsert_write_credential(
                db,
                wallet_address=wallet_address,
                key_id=str(write_key["keyId"]),
                key_secret=str(write_key["keySecret"]),
                root_path=plan.target_path,
                commit=False,
                credential_source="bootstrap",
                upstream_access_key_id=str(write_key.get("id") or "").strip() or None,
                provisioning_attempt_id=int(attempt.id),
                provisioning_mode=plan.mode,
                remote_name=str(write_key.get("name") or "").strip() or None,
                expires_at=self._parse_expiry(write_key),
            )

            if plan.create_read_credential and plan.read_name and plan.read_permissions:
                current_stage = "creating_read_key"
                read_key = self._create_access_key(
                    token=token,
                    name=plan.read_name,
                    permissions=plan.read_permissions,
                    expires_value=int(plan.read_expires_value or 0),
                    expires_unit=str(plan.read_expires_unit or "day"),
                )

                current_stage = "binding_read_key"
                self._bind_access_key(token=token, access_key_id=str(read_key["id"]), path=plan.target_path)

                current_stage = "saving_read_credential"
                read_credential = warehouse_access_service.create_read_credential(
                    db,
                    wallet_address=wallet_address,
                    key_id=str(read_key["keyId"]),
                    key_secret=str(read_key["keySecret"]),
                    root_path=plan.target_path,
                    commit=False,
                    credential_source="bootstrap",
                    upstream_access_key_id=str(read_key.get("id") or "").strip() or None,
                    provisioning_attempt_id=int(attempt.id),
                    provisioning_mode=plan.mode,
                    remote_name=str(read_key.get("name") or "").strip() or None,
                    expires_at=self._parse_expiry(read_key),
                )

            self._apply_attempt_state(
                attempt,
                stage="completed",
                status="succeeded",
                write_key=write_key,
                read_key=read_key,
                write_credential_id=getattr(write_credential, "id", None),
                read_credential_id=getattr(read_credential, "id", None),
                error_message="",
            )
            db.commit()
            db.refresh(attempt)
            return self._build_result_payload(
                attempt=attempt,
                plan=plan,
                wallet_address=wallet_address,
                write_credential=warehouse_access_service.summarize(write_credential) if write_credential is not None else None,
                read_credential=warehouse_access_service.summarize(read_credential) if read_credential is not None else None,
            )
        except Exception as exc:
            error_message = self._format_operation_error(exc, path=plan.target_path)
            if write_credential is not None and read_credential is None and plan.create_read_credential:
                self._apply_attempt_state(
                    attempt,
                    stage=current_stage,
                    status="partial_success",
                    write_key=write_key,
                    read_key=read_key,
                    write_credential_id=getattr(write_credential, "id", None),
                    read_credential_id=None,
                    error_message=error_message,
                )
                db.commit()
                db.refresh(attempt)
                raise WarehouseBootstrapExecutionError(
                    self._build_result_payload(
                        attempt=attempt,
                        plan=plan,
                        wallet_address=wallet_address,
                        write_credential=warehouse_access_service.summarize(write_credential),
                        read_credential=None,
                        error_message=error_message,
                    )
                ) from exc

            db.rollback()
            attempt = db.get(WarehouseProvisioningAttempt, int(attempt.id))
            if attempt is None:
                raise
            self._apply_attempt_state(
                attempt,
                stage=current_stage,
                status="failed",
                write_key=write_key,
                read_key=read_key,
                write_credential_id=None,
                read_credential_id=None,
                error_message=error_message,
            )
            db.commit()
            db.refresh(attempt)
            raise WarehouseBootstrapExecutionError(
                self._build_result_payload(
                    attempt=attempt,
                    plan=plan,
                    wallet_address=wallet_address,
                    write_credential=None,
                    read_credential=None,
                    error_message=error_message,
                )
            ) from exc

    def _build_plan(self, mode: WarehouseBootstrapMode) -> WarehouseBootstrapPlan:
        nonce = format(int(time() * 1000), "x")
        prefix = str(self.settings.warehouse_bootstrap_key_name_prefix or "knowledge").strip() or "knowledge"
        if mode == "app_root_write":
            return WarehouseBootstrapPlan(
                mode=mode,
                mode_label="app 根写凭证",
                target_path=warehouse_app_root(self.settings),
                create_read_credential=False,
                write_name=f"{prefix}-app-root-write-{nonce}",
                write_permissions=["read", "create", "update"],
                write_expires_value=int(self.settings.warehouse_bootstrap_write_expires_value),
                write_expires_unit=str(self.settings.warehouse_bootstrap_write_expires_unit or "day"),
            )
        return WarehouseBootstrapPlan(
            mode="uploads_bundle",
            mode_label="uploads 读写凭证",
            target_path=warehouse_default_upload_dir(self.settings),
            create_read_credential=True,
            write_name=f"{prefix}-uploads-write-{nonce}",
            write_permissions=["read", "create", "update"],
            write_expires_value=int(self.settings.warehouse_bootstrap_write_expires_value),
            write_expires_unit=str(self.settings.warehouse_bootstrap_write_expires_unit or "day"),
            read_name=f"{prefix}-uploads-read-{nonce}",
            read_permissions=["read"],
            read_expires_value=int(self.settings.warehouse_bootstrap_read_expires_value),
            read_expires_unit=str(self.settings.warehouse_bootstrap_read_expires_unit or "day"),
        )

    def _verify_signature(self, wallet_address: str, signature: str) -> str:
        warehouse_address = self._warehouse_wallet_address(wallet_address)
        payload = self._request_json(
            "POST",
            "/api/v1/public/auth/verify",
            json_body={"address": warehouse_address, "signature": signature},
        )
        data = self._extract_data(payload)
        token = str(data.get("token") or "").strip()
        if not token:
            raise WarehouseBootstrapError("warehouse verify 返回缺少 token。")
        return token

    @staticmethod
    def _warehouse_wallet_address(wallet_address: str) -> str:
        candidate = str(wallet_address or "").strip()
        if not candidate:
            raise WarehouseBootstrapError("knowledge 当前登录钱包地址为空，请重新登录。")
        try:
            return to_checksum_address(candidate)
        except Exception as exc:  # noqa: BLE001
            raise WarehouseBootstrapError(
                f"knowledge 当前登录钱包地址不是有效的 EVM 地址：{candidate}。请退出后重新用正确的钱包地址登录。"
            ) from exc

    def _create_access_key(
        self,
        *,
        token: str,
        name: str,
        permissions: list[str],
        expires_value: int,
        expires_unit: str,
    ) -> dict[str, object]:
        payload = self._request_json(
            "POST",
            "/api/v1/public/webdav/access-keys/create",
            token=token,
            json_body={
                "name": name,
                "permissions": permissions,
                "expiresValue": int(expires_value),
                "expiresUnit": str(expires_unit or "day"),
            },
        )
        key_id = str(payload.get("keyId") or "").strip()
        key_secret = str(payload.get("keySecret") or "").strip()
        access_key_id = str(payload.get("id") or "").strip()
        if not access_key_id or not key_id or not key_secret:
            raise WarehouseBootstrapError("warehouse 创建 access key 返回缺少 id / keyId / keySecret。")
        return payload

    def _bind_access_key(self, *, token: str, access_key_id: str, path: str) -> None:
        self._request_json(
            "POST",
            "/api/v1/public/webdav/access-keys/bind",
            token=token,
            json_body={"id": access_key_id, "path": path},
        )

    def _revoke_access_key(self, *, token: str, access_key_id: str) -> str:
        try:
            self._request_json(
                "POST",
                "/api/v1/public/webdav/access-keys/revoke",
                token=token,
                json_body={"id": access_key_id},
            )
            return "revoked"
        except WarehouseBootstrapError as exc:
            message = str(exc).lower()
            if exc.status == 400 and "already revoked" in message:
                return "already_revoked"
            if exc.status == 404 and "not found" in message:
                return "not_found"
            raise

    def _ensure_directory_chain(self, *, token: str, target_path: str) -> None:
        self.warehouse_gateway.ensure_app_space(
            wallet_address="",
            auth=WarehouseRequestAuth.bearer(token),
            base_path=warehouse_app_root(self.settings),
            target_path=target_path,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        url = f"{self.settings.warehouse_base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = httpx.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            raise WarehouseBootstrapError("knowledge 后端无法访问 warehouse，请检查网络配置。") from exc
        payload = self._read_payload(response)
        if response.is_success:
            if isinstance(payload, dict):
                return payload
            raise WarehouseBootstrapError("warehouse 返回了无法解析的响应。", status=response.status_code, url=url, payload=payload)
        message = self._extract_text(payload) or f"HTTP {response.status_code}"
        raise WarehouseBootstrapError(message, status=response.status_code, url=url, payload=payload)

    @staticmethod
    def _read_payload(response: httpx.Response) -> dict[str, object] | list[object] | str | None:
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                return response.json()
            except ValueError:
                return None
        try:
            text = response.text
        except Exception:  # noqa: BLE001
            return None
        trimmed = text.strip()
        if trimmed.startswith("{") or trimmed.startswith("["):
            try:
                return json.loads(trimmed)
            except ValueError:
                return text
        return text

    @staticmethod
    def _extract_data(payload: dict[str, object] | list[object] | str | None) -> dict[str, object]:
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                return data
            return payload
        return {}

    @classmethod
    def _extract_text(cls, payload: dict[str, object] | list[object] | str | None) -> str:
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, dict):
            data = cls._extract_data(payload)
            for key in ("message", "detail"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in ("message", "detail"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _create_attempt(db: Session, *, wallet_address: str, plan: WarehouseBootstrapPlan) -> WarehouseProvisioningAttempt:
        attempt = WarehouseProvisioningAttempt(
            owner_wallet_address=wallet_address.lower(),
            mode=plan.mode,
            target_path=plan.target_path,
            status="running",
            stage="started",
        )
        db.add(attempt)
        db.commit()
        db.refresh(attempt)
        return attempt

    @staticmethod
    def _apply_attempt_state(
        attempt: WarehouseProvisioningAttempt,
        *,
        stage: str,
        status: WarehouseBootstrapStatus,
        write_key: dict[str, object] | None,
        read_key: dict[str, object] | None,
        write_credential_id: int | None,
        read_credential_id: int | None,
        error_message: str,
    ) -> None:
        details_json = dict(attempt.details_json or {})
        attempt.stage = stage
        attempt.status = status
        attempt.write_upstream_access_key_id = str(write_key.get("id") or "").strip() if write_key is not None else None
        attempt.write_key_id = str(write_key.get("keyId") or "").strip() if write_key is not None else None
        attempt.read_upstream_access_key_id = str(read_key.get("id") or "").strip() if read_key is not None else None
        attempt.read_key_id = str(read_key.get("keyId") or "").strip() if read_key is not None else None
        attempt.write_credential_id = write_credential_id
        attempt.read_credential_id = read_credential_id
        attempt.error_message = str(error_message or "")
        details_json.update({
            "write_key_created": write_key is not None,
            "read_key_created": read_key is not None,
            "write_credential_saved": write_credential_id is not None,
            "read_credential_saved": read_credential_id is not None,
        })
        details_json["cleanup_status"] = WarehouseBootstrapService._cleanup_status_for_attempt(
            status=str(status),
            details_json=details_json,
            write_key_id=attempt.write_key_id,
            read_key_id=attempt.read_key_id,
        )
        if details_json["cleanup_status"] == "manual_cleanup_required":
            details_json.setdefault(
                "cleanup_notes",
                "Remote revoke/delete API is not configured in knowledge; clean upstream keys manually.",
            )
        attempt.details_json = details_json

    @staticmethod
    def _build_result_payload(
        *,
        attempt: WarehouseProvisioningAttempt,
        plan: WarehouseBootstrapPlan,
        wallet_address: str,
        write_credential: dict[str, object] | None,
        read_credential: dict[str, object] | None,
        error_message: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempt_id": int(attempt.id),
            "status": str(attempt.status),
            "stage": str(attempt.stage),
            "mode": plan.mode,
            "mode_label": plan.mode_label,
            "wallet_address": wallet_address.lower(),
            "target_path": plan.target_path,
            "write_key_id": attempt.write_key_id,
            "read_key_id": attempt.read_key_id,
            "write_credential": write_credential,
            "read_credential": read_credential,
            "error_message": str(error_message or attempt.error_message or "") or None,
            "warnings": WarehouseBootstrapService._build_warnings(
                stage=str(attempt.stage),
                status=str(attempt.status),
                details_json={
                    "write_credential_saved": write_credential is not None,
                    "read_credential_saved": read_credential is not None,
                    "cleanup_status": WarehouseBootstrapService._cleanup_status_for_attempt(
                        status=str(attempt.status),
                        details_json={},
                        write_key_id=attempt.write_key_id,
                        read_key_id=attempt.read_key_id,
                    ),
                },
            ),
            "cleanup_status": WarehouseBootstrapService._cleanup_status_for_attempt(
                status=str(attempt.status),
                details_json={},
                write_key_id=attempt.write_key_id,
                read_key_id=attempt.read_key_id,
            ),
        }

    @staticmethod
    def summarize_attempt(attempt: WarehouseProvisioningAttempt) -> dict[str, object]:
        details_json = dict(attempt.details_json or {})
        return {
            "id": int(attempt.id),
            "mode": str(attempt.mode),
            "target_path": str(attempt.target_path),
            "status": str(attempt.status),
            "stage": str(attempt.stage),
            "write_key_id": attempt.write_key_id,
            "read_key_id": attempt.read_key_id,
            "write_credential_id": attempt.write_credential_id,
            "read_credential_id": attempt.read_credential_id,
            "error_message": str(attempt.error_message or "") or None,
            "details_json": details_json,
            "warnings": WarehouseBootstrapService._build_warnings(
                stage=str(attempt.stage),
                status=str(attempt.status),
                details_json=details_json,
            ),
            "cleanup_status": WarehouseBootstrapService._cleanup_status_for_attempt(
                status=str(attempt.status),
                details_json=details_json,
                write_key_id=attempt.write_key_id,
                read_key_id=attempt.read_key_id,
            ),
            "created_at": attempt.created_at,
            "updated_at": attempt.updated_at,
        }

    @staticmethod
    def _format_operation_error(exc: Exception, *, path: str | None = None) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            target = path or str(exc.request.url)
            if status == 401:
                return (
                    f"warehouse rejected the access key for {target}. "
                    "This usually means the ak/sk is wrong, the key is revoked or expired, or the access key has no bound directories."
                )
            if status == 403:
                return (
                    f"warehouse authenticated the access key but denied access to {target}. "
                    "Bind this directory or choose a root_path under an already bound directory, and ensure the key has the required permissions."
                )
        return str(exc)

    @staticmethod
    def _build_warnings(
        *,
        stage: str,
        status: str,
        details_json: dict[str, object],
    ) -> list[str]:
        warnings: list[str] = []
        if stage == "reused_local_credentials":
            warnings.append("reused existing local bootstrap credentials; no new upstream access key was created")
        write_saved = bool(details_json.get("write_credential_saved"))
        read_saved = bool(details_json.get("read_credential_saved"))
        if status == "partial_success" and write_saved and not read_saved:
            warnings.append(
                "write credential was saved locally, but bootstrap did not finish. Re-running bootstrap may create additional upstream keys until cleanup support lands."
            )
        if str(details_json.get("cleanup_status") or "") == "manual_cleanup_required":
            warnings.append("manual cleanup is required for the listed access keys")
        return warnings

    @staticmethod
    def _cleanup_status_for_attempt(
        *,
        status: str,
        details_json: dict[str, object],
        write_key_id: str | None,
        read_key_id: str | None,
    ) -> str:
        explicit = str(details_json.get("cleanup_status") or "").strip()
        if explicit:
            return explicit
        if status == "succeeded":
            return "not_needed"
        if write_key_id or read_key_id:
            return "manual_cleanup_required"
        return "not_needed"

    @staticmethod
    def _credential_key_payload(credential) -> dict[str, object] | None:
        if credential is None:
            return None
        return {
            "id": credential.upstream_access_key_id,
            "keyId": credential.key_id,
            "name": credential.remote_name,
        }

    @staticmethod
    def _parse_expiry(payload: dict[str, object]):
        raw = payload.get("expiresAt") or payload.get("expires_at")
        if not raw:
            return None
        text = str(raw).strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            from datetime import datetime

            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    @staticmethod
    def _mark_local_credentials_revoked(db: Session, attempt: WarehouseProvisioningAttempt) -> None:
        for credential_id in (attempt.write_credential_id, attempt.read_credential_id):
            if credential_id is None:
                continue
            credential = db.get(WarehouseAccessCredential, int(credential_id))
            if credential is None:
                continue
            credential.status = "revoked_local"
