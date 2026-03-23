from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
import httpx

from tests.helpers import configure_warehouse_credentials

from knowledge.core.settings import get_settings
from knowledge.api import routes_sources
from knowledge.main import app
from knowledge.services.warehouse_scope import warehouse_app_path, warehouse_app_root


APP_ROOT = warehouse_app_root()
SETTINGS = get_settings()


def _login(client: TestClient, account) -> str:
    challenge = client.post("/auth/challenge", json={"wallet_address": account.address}).json()
    message = encode_defunct(text=challenge["message"])
    signed = Account.sign_message(message, account.key)
    verify = client.post(
        "/auth/verify",
        json={"wallet_address": account.address, "signature": signed.signature.hex()},
    )
    verify.raise_for_status()
    return verify.json()["access_token"]


def _app_path(relative_path: str) -> str:
    return warehouse_app_path(relative_path)


def _warehouse_fs_path(wallet_address: str, warehouse_path: str) -> Path:
    return Path(SETTINGS.warehouse_mock_root) / wallet_address.lower() / warehouse_path.lstrip("/")


def test_source_registration_list_get_update_and_app_scope_validation():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb = client.post("/kbs", headers=headers, json={"name": "Source KB", "description": "source"}).json()
        kb_id = kb["id"]

        invalid = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_type": "warehouse", "source_path": "/personal/uploads", "scope_type": "directory"},
        )
        assert invalid.status_code == 400
        assert APP_ROOT in invalid.json()["detail"]

        created = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_type": "warehouse", "source_path": _app_path("library/contracts"), "scope_type": "directory"},
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["source_path"] == _app_path("library/contracts")
        assert payload["sync_status"] == "pending_sync"

        duplicate = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_type": "warehouse", "source_path": _app_path("library/contracts"), "scope_type": "directory"},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == payload["id"]

        listed = client.get(f"/kbs/{kb_id}/sources", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        fetched = client.get(f"/kbs/{kb_id}/sources/{payload['id']}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["id"] == payload["id"]

        updated = client.patch(
            f"/kbs/{kb_id}/sources/{payload['id']}",
            headers=headers,
            json={"enabled": False, "missing_policy": "retain_index_until_confirmed"},
        )
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False
        assert updated.json()["sync_status"] == "disabled"
        assert updated.json()["missing_policy"] == "retain_index_until_confirmed"

        reenabled = client.patch(
            f"/kbs/{kb_id}/sources/{payload['id']}",
            headers=headers,
            json={"enabled": True},
        )
        assert reenabled.status_code == 200
        assert reenabled.json()["enabled"] is True
        assert reenabled.json()["sync_status"] == "pending_sync"


def test_source_scan_discovers_assets_recursively_and_lists_them():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Scan KB", "description": "scan"}).json()["id"]

        upload_a = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/contracts")},
            files={"file": ("a.txt", b"contract-a", "text/plain")},
        )
        upload_b = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/contracts/nested")},
            files={"file": ("b.txt", b"contract-b", "text/plain")},
        )
        assert upload_a.status_code == 200
        assert upload_b.status_code == 200

        source = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_type": "warehouse", "source_path": _app_path("library/contracts"), "scope_type": "directory"},
        ).json()

        scanned = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        assert scanned.status_code == 200
        payload = scanned.json()
        assert payload["source"]["sync_status"] == "synced"
        assert payload["stats"]["total_assets"] == 2
        assert payload["stats"]["discovered_assets"] == 2

        assets = client.get(f"/kbs/{kb_id}/sources/{source['id']}/assets", headers=headers)
        assert assets.status_code == 200
        asset_paths = [item["asset_path"] for item in assets.json()]
        assert asset_paths == [upload_a.json()["warehouse_path"], upload_b.json()["warehouse_path"]]
        assert all(item["availability_status"] == "discovered" for item in assets.json())

        all_assets = client.get(f"/kbs/{kb_id}/assets?source_id={source['id']}", headers=headers)
        assert all_assets.status_code == 200
        assert len(all_assets.json()) == 2


def test_source_scan_marks_available_changed_and_missing_assets():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Asset Status KB", "description": "status"}).json()["id"]

        upload_a = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/updates")},
            files={"file": ("a.txt", b"version-a-1", "text/plain")},
        )
        upload_b = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/updates")},
            files={"file": ("b.txt", b"version-b-1", "text/plain")},
        )
        assert upload_a.status_code == 200
        assert upload_b.status_code == 200

        source_id = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_type": "warehouse", "source_path": _app_path("library/updates"), "scope_type": "directory"},
        ).json()["id"]

        first_scan = client.post(f"/kbs/{kb_id}/sources/{source_id}/scan", headers=headers)
        assert first_scan.status_code == 200
        assert first_scan.json()["stats"]["discovered_assets"] == 2

        second_scan = client.post(f"/kbs/{kb_id}/sources/{source_id}/scan", headers=headers)
        assert second_scan.status_code == 200
        assert second_scan.json()["stats"]["available_assets"] == 2

        file_a = _warehouse_fs_path(account.address, upload_a.json()["warehouse_path"])
        file_b = _warehouse_fs_path(account.address, upload_b.json()["warehouse_path"])
        file_a.write_text("version-a-2", encoding="utf-8")
        future_ts = time.time() + 5
        os.utime(file_a, (future_ts, future_ts))
        file_b.unlink()

        third_scan = client.post(f"/kbs/{kb_id}/sources/{source_id}/scan", headers=headers)
        assert third_scan.status_code == 200
        third_payload = third_scan.json()
        assert third_payload["stats"]["changed_assets"] == 1
        assert third_payload["stats"]["missing_assets"] == 1

        changed_assets = client.get(
            f"/kbs/{kb_id}/sources/{source_id}/assets?availability_status=changed",
            headers=headers,
        )
        assert changed_assets.status_code == 200
        assert [item["asset_path"] for item in changed_assets.json()] == [upload_a.json()["warehouse_path"]]

        missing_assets = client.get(
            f"/kbs/{kb_id}/assets?source_id={source_id}&availability_status=missing",
            headers=headers,
        )
        assert missing_assets.status_code == 200
        assert [item["asset_path"] for item in missing_assets.json()] == [upload_b.json()["warehouse_path"]]


def test_source_scan_marks_source_missing_when_root_path_is_removed():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Missing Root KB", "description": "missing"}).json()["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/to-remove")},
            files={"file": ("gone.txt", b"gone soon", "text/plain")},
        )
        assert upload.status_code == 200

        source = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_type": "warehouse", "source_path": _app_path("library/to-remove"), "scope_type": "directory"},
        ).json()

        first_scan = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        assert first_scan.status_code == 200

        shutil.rmtree(_warehouse_fs_path(account.address, _app_path("library/to-remove")))

        second_scan = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        assert second_scan.status_code == 200
        payload = second_scan.json()
        assert payload["source"]["sync_status"] == "source_missing"
        assert payload["stats"]["missing_assets"] == 1

        missing_assets = client.get(
            f"/kbs/{kb_id}/sources/{source['id']}/assets?availability_status=missing",
            headers=headers,
        )
        assert missing_assets.status_code == 200
        assert len(missing_assets.json()) == 1


def test_source_scan_returns_400_and_marks_credential_invalid_on_auth_error(monkeypatch):
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        credential_ids = configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Auth Error KB", "description": "auth"}).json()["id"]

        source = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_type": "warehouse", "source_path": _app_path("library/auth-error"), "scope_type": "directory"},
        ).json()

        def deny_browse(*args, **kwargs):
            request = httpx.Request("PROPFIND", "https://webdav.yeying.pub/dav/apps/knowledge.yeying.pub/library/auth-error")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

        monkeypatch.setattr(routes_sources.source_sync_service.asset_inventory_service.warehouse_gateway, "browse", deny_browse)

        scanned = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        assert scanned.status_code == 400
        assert "401 Unauthorized" in scanned.json()["detail"]

        write_credential = client.get("/warehouse/credentials/write", headers=headers)
        assert write_credential.status_code == 200
        assert write_credential.json()["credential"]["id"] == credential_ids["write_credential_id"]
        assert write_credential.json()["credential"]["status"] == "invalid"
