from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

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


def _create_service_principal(client: TestClient, headers: dict[str, str], *, service_id: str, display_name: str) -> tuple[dict, str]:
    response = client.post(
        "/service-principals",
        headers=headers,
        json={"service_id": service_id, "display_name": display_name, "identity_type": "api_key"},
    )
    response.raise_for_status()
    payload = response.json()
    return payload["principal"], payload["api_key"]


def _create_grant(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    *,
    principal_id: int,
    release_selection_mode: str,
    pinned_release_id: int | None = None,
) -> dict:
    body = {
        "service_principal_id": principal_id,
        "release_selection_mode": release_selection_mode,
        "default_result_mode": "compact",
    }
    if pinned_release_id is not None:
        body["pinned_release_id"] = pinned_release_id
    response = client.post(f"/kbs/{kb_id}/grants", headers=headers, json=body)
    response.raise_for_status()
    return response.json()


def _publish_release(client: TestClient, headers: dict[str, str], kb_id: int, *, version: str) -> dict:
    response = client.post(
        f"/kbs/{kb_id}/releases",
        headers=headers,
        json={"version": version, "release_note": version},
    )
    response.raise_for_status()
    return response.json()


def _upload_source_and_build_evidence(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    *,
    source_dir: str,
    file_name: str,
    content: bytes,
) -> tuple[dict, list[dict]]:
    upload = client.post(
        "/warehouse/upload",
        headers=headers,
        data={"target_dir": warehouse_app_path(source_dir)},
        files={"file": (file_name, content, "text/plain")},
    )
    upload.raise_for_status()
    source = client.post(
        f"/kbs/{kb_id}/sources",
        headers=headers,
        json={
            "source_type": "warehouse",
            "source_path": warehouse_app_path(source_dir),
            "scope_type": "directory",
        },
    )
    source.raise_for_status()
    scan = client.post(f"/kbs/{kb_id}/sources/{source.json()['id']}/scan", headers=headers)
    scan.raise_for_status()
    build = client.post(f"/kbs/{kb_id}/sources/{source.json()['id']}/build-evidence", headers=headers)
    build.raise_for_status()
    evidence = client.get(f"/kbs/{kb_id}/evidence?source_id={source.json()['id']}", headers=headers)
    evidence.raise_for_status()
    return source.json(), evidence.json()


def _create_manual_item(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    *,
    title: str,
    statement: str,
    item_type: str,
    payload: dict,
    evidence_unit_ids: list[int] | None = None,
) -> tuple[int, int]:
    response = client.post(
        f"/kbs/{kb_id}/items/manual",
        headers=headers,
        json={
            "title": title,
            "statement": statement,
            "item_type": item_type,
            "structured_payload_json": payload,
            "evidence_unit_ids": evidence_unit_ids or [],
        },
    )
    response.raise_for_status()
    body = response.json()
    return body["item"]["id"], body["current_revision"]["id"]


def _update_manual_item(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    item_id: int,
    *,
    statement: str,
    payload: dict,
    evidence_unit_ids: list[int] | None = None,
) -> int:
    response = client.patch(
        f"/kbs/{kb_id}/items/{item_id}",
        headers=headers,
        json={
            "statement": statement,
            "structured_payload_json": payload,
            "evidence_unit_ids": evidence_unit_ids,
        },
    )
    response.raise_for_status()
    return response.json()["current_revision"]["id"]


def test_service_search_endpoints_support_result_views():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Service Search KB", "description": "search"}).json()["id"]

        _source, evidence = _upload_source_and_build_evidence(
            client,
            headers,
            kb_id,
            source_dir="library/service-search",
            file_name="guide.md",
            content=b"# Release Guide\n\nPublished release is the stable surface.",
        )
        evidence_ids = [item["id"] for item in evidence]
        _create_manual_item(
            client,
            headers,
            kb_id,
            title="Release guide",
            statement="Published release is the stable surface.",
            item_type="fact",
            payload={"fact": "Published release is the stable surface."},
            evidence_unit_ids=evidence_ids[:1],
        )
        _publish_release(client, headers, kb_id, version="release-1")

        principal, api_key = _create_service_principal(
            client,
            headers,
            service_id="service-search-view",
            display_name="Service Search View",
        )
        _create_grant(client, headers, kb_id, principal_id=principal["id"], release_selection_mode="latest_published")

        compact = client.post(
            "/service/search/formal",
            headers={"X-Service-Api-Key": api_key},
            json={"kb_id": kb_id, "query": "stable surface", "result_view": "compact"},
        )
        compact.raise_for_status()
        compact_payload = compact.json()
        assert compact_payload["mode"] == "formal_only"
        assert compact_payload["result_view"] == "compact"
        assert compact_payload["hits"]
        assert compact_payload["hits"][0]["result_kind"] == "formal"
        assert compact_payload["hits"][0]["content_health_status"] in {"healthy", "stale", "source_missing"}

        referenced = client.post(
            "/service/search/formal",
            headers={"X-Service-Api-Key": api_key},
            json={"kb_id": kb_id, "query": "stable surface", "result_view": "referenced"},
        )
        referenced.raise_for_status()
        referenced_payload = referenced.json()
        assert referenced_payload["hits"][0]["result_kind"] == "formal"
        assert referenced_payload["hits"][0]["source_refs"]

        audit = client.post(
            "/service/search/evidence",
            headers={"X-Service-Api-Key": api_key},
            json={"kb_id": kb_id, "query": "stable surface", "result_view": "audit"},
        )
        audit.raise_for_status()
        audit_payload = audit.json()
        assert audit_payload["mode"] == "evidence_only"
        assert audit_payload["hits"][0]["result_kind"] == "evidence"
        assert "source_health_details" in audit_payload["hits"][0]


def test_service_search_formal_first_falls_back_to_evidence():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Fallback KB", "description": "fallback"}).json()["id"]

        _source, evidence = _upload_source_and_build_evidence(
            client,
            headers,
            kb_id,
            source_dir="library/fallback-search",
            file_name="mixed.txt",
            content=b"Published release is stable. Evidence fallback should work when formal hits are insufficient.",
        )
        _create_manual_item(
            client,
            headers,
            kb_id,
            title="Published release stability",
            statement="Published release is stable.",
            item_type="fact",
            payload={"fact": "Published release is stable."},
            evidence_unit_ids=[evidence[0]["id"]],
        )
        _publish_release(client, headers, kb_id, version="release-1")

        principal, api_key = _create_service_principal(
            client,
            headers,
            service_id="service-formal-first",
            display_name="Service Formal First",
        )
        _create_grant(client, headers, kb_id, principal_id=principal["id"], release_selection_mode="latest_published")

        response = client.post(
            "/service/search",
            headers={"X-Service-Api-Key": api_key},
            json={"kb_id": kb_id, "query": "fallback should work", "top_k": 3, "result_view": "referenced"},
        )
        response.raise_for_status()
        payload = response.json()
        assert payload["mode"] == "formal_first"
        assert payload["hits"]
        result_kinds = [item["result_kind"] for item in payload["hits"]]
        assert "evidence" in result_kinds


def test_service_search_availability_modes_filter_source_missing_results():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Availability KB", "description": "availability"}).json()["id"]

        source_dir = "library/availability-search"
        source, evidence = _upload_source_and_build_evidence(
            client,
            headers,
            kb_id,
            source_dir=source_dir,
            file_name="missing.txt",
            content=b"Source missing evidence should still be marked.",
        )
        service_principal, api_key = _create_service_principal(
            client,
            headers,
            service_id="service-availability",
            display_name="Service Availability",
        )
        _create_grant(client, headers, kb_id, principal_id=service_principal["id"], release_selection_mode="latest_published")

        missing_path = warehouse_app_path(f"{source_dir}/missing.txt")
        delete = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        delete.raise_for_status()
        from pathlib import Path
        from knowledge.core.settings import get_settings

        root = Path(get_settings().warehouse_mock_root) / account.address.lower() / missing_path.lstrip("/")
        root.unlink()
        rescan = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        rescan.raise_for_status()

        allow_all = client.post(
            "/service/search/evidence",
            headers={"X-Service-Api-Key": api_key},
            json={"kb_id": kb_id, "query": "source missing evidence", "availability_mode": "allow_all"},
        )
        allow_all.raise_for_status()
        allow_all_payload = allow_all.json()
        assert allow_all_payload["hits"]
        assert allow_all_payload["hits"][0]["content_health_status"] == "source_missing"

        healthy_only = client.post(
            "/service/search/evidence",
            headers={"X-Service-Api-Key": api_key},
            json={"kb_id": kb_id, "query": "source missing evidence", "availability_mode": "healthy_only"},
        )
        healthy_only.raise_for_status()
        assert healthy_only.json()["hits"] == []

        exclude_missing = client.post(
            "/service/search/evidence",
            headers={"X-Service-Api-Key": api_key},
            json={"kb_id": kb_id, "query": "source missing evidence", "availability_mode": "exclude_source_missing"},
        )
        exclude_missing.raise_for_status()
        assert exclude_missing.json()["hits"] == []


def test_service_search_respects_latest_published_and_pinned_release_selection():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Grant Release Search KB", "description": "grant-release"}).json()["id"]

        _source, evidence = _upload_source_and_build_evidence(
            client,
            headers,
            kb_id,
            source_dir="library/grant-release",
            file_name="fact.txt",
            content=b"Version one statement.",
        )
        item_id, revision_v1 = _create_manual_item(
            client,
            headers,
            kb_id,
            title="Release fact",
            statement="Version one statement.",
            item_type="fact",
            payload={"fact": "Version one statement."},
            evidence_unit_ids=[evidence[0]["id"]],
        )
        release_1 = _publish_release(client, headers, kb_id, version="release-1")
        release_1_id = release_1["release"]["id"]

        revision_v2 = _update_manual_item(
            client,
            headers,
            kb_id,
            item_id,
            statement="Version two statement.",
            payload={"fact": "Version two statement."},
        )
        release_2 = client.post(
            f"/kbs/{kb_id}/releases/{release_1_id}/hotfix",
            headers=headers,
            json={"version": "release-2", "release_note": "hotfix", "knowledge_item_ids": [item_id]},
        )
        release_2.raise_for_status()
        assert release_2.json()["items"][0]["knowledge_item_revision_id"] == revision_v2

        latest_principal, latest_api_key = _create_service_principal(
            client,
            headers,
            service_id="service-latest-search",
            display_name="Latest Search Service",
        )
        pinned_principal, pinned_api_key = _create_service_principal(
            client,
            headers,
            service_id="service-pinned-search",
            display_name="Pinned Search Service",
        )
        _create_grant(client, headers, kb_id, principal_id=latest_principal["id"], release_selection_mode="latest_published")
        _create_grant(
            client,
            headers,
            kb_id,
            principal_id=pinned_principal["id"],
            release_selection_mode="pinned_release",
            pinned_release_id=release_1_id,
        )

        latest = client.post(
            "/service/search/formal",
            headers={"X-Service-Api-Key": latest_api_key},
            json={"kb_id": kb_id, "query": "Version two statement."},
        )
        latest.raise_for_status()
        latest_payload = latest.json()
        assert latest_payload["release"]["id"] == release_2.json()["release"]["id"]
        assert any("Version two statement." in item.get("statement", "") for item in latest_payload["hits"])

        pinned = client.post(
            "/service/search/formal",
            headers={"X-Service-Api-Key": pinned_api_key},
            json={"kb_id": kb_id, "query": "Version one statement."},
        )
        pinned.raise_for_status()
        pinned_payload = pinned.json()
        assert pinned_payload["release"]["id"] == release_1_id
        assert any("Version one statement." in item.get("statement", "") for item in pinned_payload["hits"])
