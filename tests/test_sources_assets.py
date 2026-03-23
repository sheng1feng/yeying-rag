from __future__ import annotations

import os
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from tests.helpers import configure_warehouse_credentials

from knowledge.core.settings import get_settings
from knowledge.main import app
from knowledge.services.warehouse_scope import warehouse_app_path


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


def _mock_path(wallet_address: str, warehouse_path: str) -> Path:
    settings = get_settings()
    return Path(settings.warehouse_mock_root) / wallet_address.lower() / warehouse_path.lstrip("/")


def test_source_registration_and_scan_discovers_assets():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)

        kb = client.post("/kbs", headers=headers, json={"name": "Source KB", "description": "sources"}).json()
        kb_id = kb["id"]

        upload_a = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": warehouse_app_path("library/source-a")},
            files={"file": ("alpha.txt", b"alpha asset", "text/plain")},
        )
        upload_b = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": warehouse_app_path("library/source-a/nested")},
            files={"file": ("beta.txt", b"beta asset", "text/plain")},
        )
        assert upload_a.status_code == 200
        assert upload_b.status_code == 200

        created = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_path": warehouse_app_path("library/source-a"), "scope_type": "directory"},
        )
        assert created.status_code == 200
        source = created.json()
        assert source["source_type"] == "warehouse"
        assert source["sync_status"] == "pending_sync"

        duplicate = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_path": warehouse_app_path("library/source-a"), "scope_type": "directory"},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == source["id"]

        scan = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        assert scan.status_code == 200
        scan_payload = scan.json()
        assert scan_payload["source"]["sync_status"] == "synced"
        assert scan_payload["stats"]["total_assets"] == 2
        assert scan_payload["stats"]["discovered_assets"] == 2

        sources = client.get(f"/kbs/{kb_id}/sources", headers=headers)
        assert sources.status_code == 200
        assert len(sources.json()) == 1

        assets = client.get(f"/kbs/{kb_id}/sources/{source['id']}/assets", headers=headers)
        assert assets.status_code == 200
        asset_paths = [item["asset_path"] for item in assets.json()]
        assert upload_a.json()["warehouse_path"] in asset_paths
        assert upload_b.json()["warehouse_path"] in asset_paths
        assert all(item["availability_status"] == "discovered" for item in assets.json())


def test_source_registration_enforces_app_only_scope_and_update():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb = client.post("/kbs", headers=headers, json={"name": "Scope KB", "description": "scope"}).json()
        kb_id = kb["id"]

        invalid = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_path": "/personal/uploads/not-allowed", "scope_type": "directory"},
        )
        assert invalid.status_code == 400
        assert warehouse_app_path("") in invalid.json()["detail"]

        created = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_path": warehouse_app_path("uploads/source-b"), "scope_type": "directory"},
        )
        assert created.status_code == 200
        source_id = created.json()["id"]

        updated = client.patch(
            f"/kbs/{kb_id}/sources/{source_id}",
            headers=headers,
            json={"enabled": False, "missing_policy": "retain_index_until_confirmed"},
        )
        assert updated.status_code == 200
        payload = updated.json()
        assert payload["enabled"] is False
        assert payload["missing_policy"] == "retain_index_until_confirmed"


def test_source_scan_marks_changed_and_missing_assets():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb = client.post("/kbs", headers=headers, json={"name": "Change KB", "description": "change"}).json()
        kb_id = kb["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": warehouse_app_path("library/source-c")},
            files={"file": ("gamma.txt", b"original asset", "text/plain")},
        )
        assert upload.status_code == 200
        source_path = warehouse_app_path("library/source-c")
        created = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_path": source_path, "scope_type": "directory"},
        )
        assert created.status_code == 200
        source_id = created.json()["id"]

        first_scan = client.post(f"/kbs/{kb_id}/sources/{source_id}/scan", headers=headers)
        assert first_scan.status_code == 200

        local_file = _mock_path(account.address, upload.json()["warehouse_path"])
        local_file.write_text("updated asset", encoding="utf-8")
        next_mtime = local_file.stat().st_mtime + 5
        os.utime(local_file, (next_mtime, next_mtime))

        second_scan = client.post(f"/kbs/{kb_id}/sources/{source_id}/scan", headers=headers)
        assert second_scan.status_code == 200
        assert second_scan.json()["stats"]["changed_assets"] == 1

        assets_after_change = client.get(
            f"/kbs/{kb_id}/assets",
            headers=headers,
            params={"source_id": source_id, "availability_status": "changed"},
        )
        assert assets_after_change.status_code == 200
        assert len(assets_after_change.json()) == 1
        assert assets_after_change.json()[0]["availability_status"] == "changed"

        local_file.unlink()

        third_scan = client.post(f"/kbs/{kb_id}/sources/{source_id}/scan", headers=headers)
        assert third_scan.status_code == 200
        assert third_scan.json()["stats"]["missing_assets"] == 1

        missing_assets = client.get(
            f"/kbs/{kb_id}/sources/{source_id}/assets",
            headers=headers,
            params={"availability_status": "missing"},
        )
        assert missing_assets.status_code == 200
        assert len(missing_assets.json()) == 1
        assert missing_assets.json()[0]["availability_status"] == "missing"


def test_source_scan_marks_source_missing_when_root_removed():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb = client.post("/kbs", headers=headers, json={"name": "Missing Root KB", "description": "missing-root"}).json()
        kb_id = kb["id"]

        source_path = warehouse_app_path("library/missing-root")
        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": source_path},
            files={"file": ("root.txt", b"root asset", "text/plain")},
        )
        assert upload.status_code == 200

        created = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_path": source_path, "scope_type": "directory"},
        )
        assert created.status_code == 200
        source_id = created.json()["id"]
        initial_scan = client.post(f"/kbs/{kb_id}/sources/{source_id}/scan", headers=headers)
        assert initial_scan.status_code == 200

        local_root = _mock_path(account.address, source_path)
        for child in sorted(local_root.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        local_root.rmdir()

        scan_after_delete = client.post(f"/kbs/{kb_id}/sources/{source_id}/scan", headers=headers)
        assert scan_after_delete.status_code == 200
        payload = scan_after_delete.json()
        assert payload["source"]["sync_status"] == "source_missing"
        assert payload["stats"]["missing_assets"] == 1
