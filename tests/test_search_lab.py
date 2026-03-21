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


def _warehouse_fs_path(wallet_address: str, warehouse_path: str) -> Path:
    settings = get_settings()
    return Path(settings.warehouse_mock_root) / wallet_address.lower() / warehouse_path.lstrip("/")


def _upload_source_and_build_evidence(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    *,
    source_dir: str,
    file_name: str,
    content: bytes,
) -> tuple[dict, list[dict], dict]:
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
    return source.json(), evidence.json(), upload.json()


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


def _publish_release(client: TestClient, headers: dict[str, str], kb_id: int, *, version: str) -> dict:
    response = client.post(
        f"/kbs/{kb_id}/releases",
        headers=headers,
        json={"version": version, "release_note": version},
    )
    response.raise_for_status()
    return response.json()


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
    release_selection_mode: str = "latest_published",
    pinned_release_id: int | None = None,
) -> dict:
    body = {
        "service_principal_id": principal_id,
        "release_selection_mode": release_selection_mode,
        "default_result_mode": "audit",
    }
    if pinned_release_id is not None:
        body["pinned_release_id"] = pinned_release_id
    response = client.post(f"/kbs/{kb_id}/grants", headers=headers, json=body)
    response.raise_for_status()
    return response.json()


def test_service_search_writes_retrieval_logs_and_log_detail_is_auditable():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Log KB", "description": "log"}).json()["id"]

        _source, evidence, _upload = _upload_source_and_build_evidence(
            client,
            headers,
            kb_id,
            source_dir="library/log-search",
            file_name="fact.txt",
            content=b"Published release is the stable truth.",
        )
        _create_manual_item(
            client,
            headers,
            kb_id,
            title="Stable truth",
            statement="Published release is the stable truth.",
            item_type="fact",
            payload={"fact": "Published release is the stable truth."},
            evidence_unit_ids=[evidence[0]["id"]],
        )
        release = _publish_release(client, headers, kb_id, version="release-1")
        principal, api_key = _create_service_principal(
            client,
            headers,
            service_id="service-log-search",
            display_name="Service Log Search",
        )
        grant = _create_grant(client, headers, kb_id, principal_id=principal["id"])

        search = client.post(
            "/service/search",
            headers={"X-Service-Api-Key": api_key},
            json={"kb_id": kb_id, "query": "stable truth", "result_view": "audit"},
        )
        search.raise_for_status()

        logs = client.get(f"/kbs/{kb_id}/retrieval-logs", headers=headers)
        logs.raise_for_status()
        logs_payload = logs.json()
        assert logs_payload, "expected retrieval log after service search"
        latest_log = logs_payload[0]
        assert latest_log["query"] == "stable truth"
        assert latest_log["query_mode"] == "formal_first"
        assert latest_log["release_id"] == release["release"]["id"]
        assert latest_log["service_grant_id"] == grant["id"]
        assert latest_log["service_principal_id"] == principal["id"]
        assert latest_log["result_summary_json"]["hit_count"] >= 1
        assert latest_log["trace_json"]["mode"] == "formal_first"

        detail = client.get(f"/kbs/{kb_id}/retrieval-logs/{latest_log['id']}", headers=headers)
        detail.raise_for_status()
        detail_payload = detail.json()
        assert detail_payload["id"] == latest_log["id"]
        assert detail_payload["trace_json"]["result_view"] == "audit"
        assert "hits" in detail_payload["trace_json"]


def test_search_lab_compare_returns_formal_evidence_and_formal_first_views():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Compare KB", "description": "compare"}).json()["id"]

        _source, evidence, _upload = _upload_source_and_build_evidence(
            client,
            headers,
            kb_id,
            source_dir="library/compare-search",
            file_name="guide.md",
            content=b"# Guide\n\nPublished release is stable. Evidence fallback should work.",
        )
        _create_manual_item(
            client,
            headers,
            kb_id,
            title="Guide fact",
            statement="Published release is stable.",
            item_type="fact",
            payload={"fact": "Published release is stable."},
            evidence_unit_ids=[evidence[0]["id"]],
        )
        _publish_release(client, headers, kb_id, version="release-1")

        compare = client.post(
            f"/kbs/{kb_id}/search-lab/compare",
            headers=headers,
            json={"query": "evidence fallback", "top_k": 3, "result_view": "audit", "availability_mode": "allow_all"},
        )
        compare.raise_for_status()
        payload = compare.json()
        assert "formal_only" in payload
        assert "evidence_only" in payload
        assert "formal_first" in payload
        assert payload["formal_only"]["mode"] == "formal_only"
        assert payload["evidence_only"]["mode"] == "evidence_only"
        assert payload["formal_first"]["mode"] == "formal_first"
        assert payload["formal_only"]["hits"][0]["result_kind"] == "formal"
        assert payload["evidence_only"]["hits"][0]["result_kind"] == "evidence"
        assert payload["formal_first"]["hits"], "formal_first should return comparable results"


def test_source_governance_exposes_source_missing_and_service_search_keeps_audit_markers():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Governance KB", "description": "governance"}).json()["id"]

        source, evidence, upload = _upload_source_and_build_evidence(
            client,
            headers,
            kb_id,
            source_dir="library/governance-search",
            file_name="missing.txt",
            content=b"Source missing should remain auditable.",
        )
        _create_manual_item(
            client,
            headers,
            kb_id,
            title="Missing source fact",
            statement="Source missing should remain auditable.",
            item_type="fact",
            payload={"fact": "Source missing should remain auditable."},
            evidence_unit_ids=[evidence[0]["id"]],
        )
        _publish_release(client, headers, kb_id, version="release-1")
        principal, api_key = _create_service_principal(
            client,
            headers,
            service_id="service-governance-search",
            display_name="Service Governance Search",
        )
        _create_grant(client, headers, kb_id, principal_id=principal["id"])

        _warehouse_fs_path(account.address, upload["warehouse_path"]).unlink()
        rescan = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        rescan.raise_for_status()

        search = client.post(
            "/service/search/formal",
            headers={"X-Service-Api-Key": api_key},
            json={"kb_id": kb_id, "query": "auditable", "result_view": "audit", "availability_mode": "allow_all"},
        )
        search.raise_for_status()
        payload = search.json()
        assert payload["hits"], "source_missing result should still be returned in allow_all"
        assert payload["hits"][0]["content_health_status"] == "source_missing"
        assert payload["hits"][0]["source_health_details"]

        governance = client.get(f"/kbs/{kb_id}/source-governance", headers=headers)
        governance.raise_for_status()
        governance_payload = governance.json()
        assert governance_payload["status_counts"]["source_missing"] >= 1
        assert any(item["availability_status"] == "missing" for item in governance_payload["assets"])


def test_search_lab_compare_explains_stale_penalty_or_ranks_healthy_before_stale():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)
        kb_id = client.post("/kbs", headers=headers, json={"name": "Stale KB", "description": "stale"}).json()["id"]

        source_a, evidence_a, upload_a = _upload_source_and_build_evidence(
            client,
            headers,
            kb_id,
            source_dir="library/stale-search/a",
            file_name="healthy.txt",
            content=b"Stable release scoring for governance.",
        )
        source_b, evidence_b, upload_b = _upload_source_and_build_evidence(
            client,
            headers,
            kb_id,
            source_dir="library/stale-search/b",
            file_name="stale.txt",
            content=b"Stable release scoring for governance.",
        )
        _create_manual_item(
            client,
            headers,
            kb_id,
            title="Healthy item",
            statement="Stable release scoring for governance.",
            item_type="fact",
            payload={"fact": "Stable release scoring for governance."},
            evidence_unit_ids=[evidence_a[0]["id"]],
        )
        _create_manual_item(
            client,
            headers,
            kb_id,
            title="Stale item",
            statement="Stable release scoring for governance.",
            item_type="fact",
            payload={"fact": "Stable release scoring for governance."},
            evidence_unit_ids=[evidence_b[0]["id"]],
        )
        _publish_release(client, headers, kb_id, version="release-1")

        stale_file = _warehouse_fs_path(account.address, upload_b["warehouse_path"])
        stale_file.write_text("Stable release scoring for governance. updated", encoding="utf-8")
        future_ts = stale_file.stat().st_mtime + 5
        os.utime(stale_file, (future_ts, future_ts))
        rescan = client.post(f"/kbs/{kb_id}/sources/{source_b['id']}/scan", headers=headers)
        rescan.raise_for_status()

        compare = client.post(
            f"/kbs/{kb_id}/search-lab/compare",
            headers=headers,
            json={"query": "stable release scoring", "top_k": 5, "result_view": "audit", "availability_mode": "allow_all"},
        )
        compare.raise_for_status()
        hits = compare.json()["formal_only"]["hits"]
        assert len(hits) >= 2
        assert hits[0]["content_health_status"] == "healthy"
        stale_hit = next(item for item in hits if item["content_health_status"] == "stale")
        healthy_hit = next(item for item in hits if item["content_health_status"] == "healthy")
        assert hits.index(healthy_hit) < hits.index(stale_hit)
