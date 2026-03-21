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


def _app_path(relative_path: str) -> str:
    return warehouse_app_path(relative_path)


def _warehouse_fs_path(wallet_address: str, warehouse_path: str) -> Path:
    settings = get_settings()
    return Path(settings.warehouse_mock_root) / wallet_address.lower() / warehouse_path.lstrip("/")


def _create_source_and_scan(client: TestClient, headers: dict[str, str], kb_id: int, source_path: str) -> tuple[dict, list[dict]]:
    source = client.post(
        f"/kbs/{kb_id}/sources",
        headers=headers,
        json={"source_type": "warehouse", "source_path": source_path, "scope_type": "directory"},
    )
    source.raise_for_status()
    scan = client.post(f"/kbs/{kb_id}/sources/{source.json()['id']}/scan", headers=headers)
    scan.raise_for_status()
    assets = client.get(f"/kbs/{kb_id}/sources/{source.json()['id']}/assets", headers=headers)
    assets.raise_for_status()
    return source.json(), assets.json()


def test_build_evidence_for_source_creates_evidence_units_with_locators():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Evidence KB", "description": "evidence"}).json()["id"]

        markdown_upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/evidence-source")},
            files={"file": ("notes.md", b"# Title\n\n## Section\n\nEvidence body", "text/markdown")},
        )
        json_upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/evidence-source")},
            files={"file": ("facts.json", b'{\"fact\": \"value\", \"items\": [1, 2]}', "application/json")},
        )
        assert markdown_upload.status_code == 200
        assert json_upload.status_code == 200

        source, assets = _create_source_and_scan(client, headers, kb_id, _app_path("library/evidence-source"))
        assert len(assets) == 2

        build = client.post(f"/kbs/{kb_id}/sources/{source['id']}/build-evidence", headers=headers)
        assert build.status_code == 200
        payload = build.json()
        assert payload["processed_asset_count"] == 2
        assert payload["built_evidence_count"] >= 2
        assert payload["skipped_asset_count"] == 0

        evidence = client.get(f"/kbs/{kb_id}/evidence?source_id={source['id']}", headers=headers)
        assert evidence.status_code == 200
        items = evidence.json()
        assert len(items) >= 2
        assert {item["evidence_type"] for item in items}.issuperset({"markdown_section", "json_record"})
        assert all(item["vector_status"] == "indexed" for item in items)
        assert all(item["source_locator"]["asset_path"].startswith(_app_path("library/evidence-source")) for item in items)
        assert all("chunk_index" in item["source_locator"] for item in items)
        assert all("file_type" in item["source_locator"] for item in items)
        markdown_items = [item for item in items if item["evidence_type"] == "markdown_section"]
        assert markdown_items
        assert any("section_path" in item["source_locator"] for item in markdown_items)

        detail = client.get(f"/kbs/{kb_id}/evidence/{items[0]['id']}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["id"] == items[0]["id"]


def test_rebuild_evidence_for_asset_replaces_existing_units_after_change():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Evidence Rebuild KB", "description": "rebuild"}).json()["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/rebuild-source")},
            files={"file": ("story.txt", b"original evidence body", "text/plain")},
        )
        assert upload.status_code == 200

        source, assets = _create_source_and_scan(client, headers, kb_id, _app_path("library/rebuild-source"))
        asset_id = assets[0]["id"]

        first_build = client.post(f"/kbs/{kb_id}/assets/{asset_id}/build-evidence", headers=headers)
        assert first_build.status_code == 200
        evidence_before = client.get(f"/kbs/{kb_id}/evidence?asset_id={asset_id}", headers=headers)
        assert evidence_before.status_code == 200
        before_items = evidence_before.json()
        assert before_items

        local_file = _warehouse_fs_path(account.address, upload.json()["warehouse_path"])
        local_file.write_text("updated evidence body with new text", encoding="utf-8")
        next_mtime = local_file.stat().st_mtime + 5
        os.utime(local_file, (next_mtime, next_mtime))

        rescan = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        assert rescan.status_code == 200
        assert rescan.json()["stats"]["changed_assets"] == 1

        second_build = client.post(f"/kbs/{kb_id}/assets/{asset_id}/build-evidence", headers=headers)
        assert second_build.status_code == 200
        evidence_after = client.get(f"/kbs/{kb_id}/evidence?asset_id={asset_id}", headers=headers)
        assert evidence_after.status_code == 200
        after_items = evidence_after.json()
        assert after_items
        assert len(after_items) == len(before_items)
        assert sum(1 for item in after_items if "updated evidence body" in item["text"]) >= 1
        assert any("updated evidence body" in item["text"] for item in after_items)


def test_source_build_skips_missing_assets_and_explicit_missing_asset_build_fails():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Evidence Missing KB", "description": "missing"}).json()["id"]

        upload_a = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/missing-evidence")},
            files={"file": ("keep.txt", b"keep me", "text/plain")},
        )
        upload_b = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("library/missing-evidence")},
            files={"file": ("gone.txt", b"remove me", "text/plain")},
        )
        assert upload_a.status_code == 200
        assert upload_b.status_code == 200

        source, assets = _create_source_and_scan(client, headers, kb_id, _app_path("library/missing-evidence"))
        gone_asset = next(item for item in assets if item["asset_name"] == "gone.txt")

        _warehouse_fs_path(account.address, upload_b.json()["warehouse_path"]).unlink()
        rescan = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        assert rescan.status_code == 200
        assert rescan.json()["stats"]["missing_assets"] == 1

        build_source = client.post(f"/kbs/{kb_id}/sources/{source['id']}/build-evidence", headers=headers)
        assert build_source.status_code == 200
        assert build_source.json()["processed_asset_count"] == 1
        assert build_source.json()["skipped_asset_count"] == 1

        missing_build = client.post(f"/kbs/{kb_id}/assets/{gone_asset['id']}/build-evidence", headers=headers)
        assert missing_build.status_code == 400
        assert "not eligible" in missing_build.json()["detail"]

        missing_evidence = client.get(f"/kbs/{kb_id}/evidence?asset_id={gone_asset['id']}", headers=headers)
        assert missing_evidence.status_code == 200
        assert missing_evidence.json() == []
