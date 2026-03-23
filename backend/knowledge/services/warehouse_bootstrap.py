from __future__ import annotations

from dataclasses import dataclass
import json
from time import time
from typing import Literal

import httpx
from eth_utils import to_checksum_address
from sqlalchemy.orm import Session

from knowledge.core.settings import Settings, get_settings
from knowledge.services.warehouse import WarehouseGateway, WarehouseRequestAuth, build_warehouse_gateway
from knowledge.services.warehouse_access import WarehouseAccessService
from knowledge.services.warehouse_scope import warehouse_app_root, warehouse_default_upload_dir


WarehouseBootstrapMode = Literal["uploads_bundle", "app_root_write"]


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
    read_name: str | None = None
    read_permissions: list[str] | None = None


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
        token = self._verify_signature(wallet_address, signature)

        write_key = self._create_access_key(
            token=token,
            name=plan.write_name,
            permissions=plan.write_permissions,
        )
        self._bind_access_key(token=token, access_key_id=str(write_key["id"]), path=plan.target_path)
        self._ensure_directory_chain(token=token, target_path=plan.target_path)

        write_credential = warehouse_access_service.upsert_write_credential(
            db,
            wallet_address=wallet_address,
            key_id=str(write_key["keyId"]),
            key_secret=str(write_key["keySecret"]),
            root_path=plan.target_path,
        )
        result: dict[str, object] = {
            "mode": plan.mode,
            "mode_label": plan.mode_label,
            "wallet_address": wallet_address.lower(),
            "target_path": plan.target_path,
            "write_key_id": str(write_key["keyId"]),
            "write_credential": warehouse_access_service.summarize(write_credential),
        }

        if plan.create_read_credential and plan.read_name and plan.read_permissions:
            read_key = self._create_access_key(
                token=token,
                name=plan.read_name,
                permissions=plan.read_permissions,
            )
            self._bind_access_key(token=token, access_key_id=str(read_key["id"]), path=plan.target_path)
            read_credential = warehouse_access_service.create_read_credential(
                db,
                wallet_address=wallet_address,
                key_id=str(read_key["keyId"]),
                key_secret=str(read_key["keySecret"]),
                root_path=plan.target_path,
            )
            result["read_key_id"] = str(read_key["keyId"])
            result["read_credential"] = warehouse_access_service.summarize(read_credential)

        return result

    def _build_plan(self, mode: WarehouseBootstrapMode) -> WarehouseBootstrapPlan:
        nonce = format(int(time() * 1000), "x")
        if mode == "app_root_write":
            return WarehouseBootstrapPlan(
                mode=mode,
                mode_label="app 根写凭证",
                target_path=warehouse_app_root(self.settings),
                create_read_credential=False,
                write_name=f"knowledge-app-root-write-{nonce}",
                write_permissions=["read", "create", "update"],
            )
        return WarehouseBootstrapPlan(
            mode="uploads_bundle",
            mode_label="uploads 读写凭证",
            target_path=warehouse_default_upload_dir(self.settings),
            create_read_credential=True,
            write_name=f"knowledge-uploads-write-{nonce}",
            write_permissions=["read", "create", "update"],
            read_name=f"knowledge-uploads-read-{nonce}",
            read_permissions=["read"],
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

    def _create_access_key(self, *, token: str, name: str, permissions: list[str]) -> dict[str, object]:
        payload = self._request_json(
            "POST",
            "/api/v1/public/webdav/access-keys/create",
            token=token,
            json_body={
                "name": name,
                "permissions": permissions,
                "expiresValue": 0,
                "expiresUnit": "day",
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
