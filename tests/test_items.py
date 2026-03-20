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


def _create_evidence_ready_source(client: TestClient, headers: dict[str, str], kb_id: int, relative_dir: str, file_name: str, content: bytes) -> tuple[dict, list[dict]]:
    upload = client.post(
        "/warehouse/upload",
        headers=headers,
        data={"target_dir": warehouse_app_path(relative_dir)},
        files={"file": (file_name, content, "text/plain")},
    )
    upload.raise_for_status()
    source = client.post(
        f"/kbs/{kb_id}/sources",
        headers=headers,
        json={"source_type": "warehouse", "source_path": warehouse_app_path(relative_dir), "scope_type": "directory"},
    )
    source.raise_for_status()
    scan = client.post(f"/kbs/{kb_id}/sources/{source.json()['id']}/scan", headers=headers)
    scan.raise_for_status()
    build = client.post(f"/kbs/{kb_id}/sources/{source.json()['id']}/build-evidence", headers=headers)
    build.raise_for_status()
    evidence = client.get(f"/kbs/{kb_id}/evidence?source_id={source.json()['id']}", headers=headers)
    evidence.raise_for_status()
    return source.json(), evidence.json()


def test_candidate_generation_from_source_and_accept_to_formal_item():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Candidate KB", "description": "candidate"}).json()["id"]

        source, evidence = _create_evidence_ready_source(
            client,
            headers,
            kb_id,
            "library/candidate-source",
            "faq.txt",
            b"Q: What is the release truth?\nA: Published release is the only truth.",
        )
        assert evidence

        generated = client.post(f"/kbs/{kb_id}/sources/{source['id']}/generate-candidates", headers=headers)
        assert generated.status_code == 200
        payload = generated.json()
        assert payload["created_count"] >= 1
        assert payload["candidates"]
        candidate = payload["candidates"][0]
        assert candidate["item_type"] == "faq"
        assert candidate["review_status"] == "pending_review"

        accepted = client.post(
            f"/kbs/{kb_id}/candidates/{candidate['id']}/accept",
            headers=headers,
            json={},
        )
        assert accepted.status_code == 200
        accepted_payload = accepted.json()
        assert accepted_payload["item"]["lifecycle_status"] == "confirmed"
        assert accepted_payload["item"]["origin_type"] == "extracted"
        assert accepted_payload["current_revision"]["review_status"] == "accepted"
        assert accepted_payload["current_revision"]["visibility_status"] == "active"
        assert len(accepted_payload["current_revision"]["evidence_links"]) == 1

        candidates = client.get(f"/kbs/{kb_id}/candidates", headers=headers)
        assert candidates.status_code == 200
        assert candidates.json()[0]["review_status"] == "accepted"

        items = client.get(f"/kbs/{kb_id}/items", headers=headers)
        assert items.status_code == 200
        assert len(items.json()) == 1


def test_manual_item_create_records_manual_provenance_and_evidence_links():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Manual KB", "description": "manual"}).json()["id"]

        _source, evidence = _create_evidence_ready_source(
            client,
            headers,
            kb_id,
            "library/manual-source",
            "rule.txt",
            b"Rule: All service reads must use published releases.",
        )
        evidence_id = evidence[0]["id"]

        created = client.post(
            f"/kbs/{kb_id}/items/manual",
            headers=headers,
            json={
                "title": "Published release rule",
                "statement": "All service reads must use published releases.",
                "item_type": "rule",
                "structured_payload_json": {"rule": "All service reads must use published releases."},
                "evidence_unit_ids": [evidence_id],
                "source_note": "manual seed",
            },
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["item"]["origin_type"] == "manual"
        assert payload["current_revision"]["provenance_type"] == "manual"
        assert payload["current_revision"]["provenance_json"]["created_via"] == "manual"
        assert payload["current_revision"]["provenance_json"]["evidence_unit_ids"] == [evidence_id]
        assert len(payload["current_revision"]["evidence_links"]) == 1


def test_manual_item_update_creates_new_revision_and_keeps_multiple_evidence_links():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Revision KB", "description": "revisions"}).json()["id"]

        source, evidence = _create_evidence_ready_source(
            client,
            headers,
            kb_id,
            "library/revision-source",
            "procedure.txt",
            b"1. Open release page\n2. Verify published snapshot",
        )
        extra_upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": warehouse_app_path("library/revision-source")},
            files={"file": ("note.txt", b"Rule: Never read workspace by default.", "text/plain")},
        )
        extra_upload.raise_for_status()
        rescan = client.post(f"/kbs/{kb_id}/sources/{source['id']}/scan", headers=headers)
        rescan.raise_for_status()
        rebuild = client.post(f"/kbs/{kb_id}/sources/{source['id']}/build-evidence", headers=headers)
        rebuild.raise_for_status()
        all_evidence = client.get(f"/kbs/{kb_id}/evidence?source_id={source['id']}", headers=headers)
        all_evidence.raise_for_status()
        evidence_ids = [item["id"] for item in all_evidence.json()]
        assert len(evidence_ids) >= 2

        created = client.post(
            f"/kbs/{kb_id}/items/manual",
            headers=headers,
            json={
                "title": "Release verification procedure",
                "statement": "Verify published snapshots before service reads.",
                "item_type": "procedure",
                "structured_payload_json": {"steps": ["Open release page", "Verify published snapshot"]},
                "evidence_unit_ids": [evidence_ids[0]],
            },
        )
        assert created.status_code == 200
        item_id = created.json()["item"]["id"]
        assert created.json()["current_revision"]["revision_no"] == 1

        updated = client.patch(
            f"/kbs/{kb_id}/items/{item_id}",
            headers=headers,
            json={
                "statement": "Verify published snapshots and avoid workspace reads.",
                "structured_payload_json": {
                    "steps": ["Open release page", "Verify published snapshot", "Avoid workspace reads"],
                },
                "evidence_unit_ids": evidence_ids[:2],
            },
        )
        assert updated.status_code == 200
        payload = updated.json()
        assert payload["item"]["current_revision_id"] == payload["current_revision"]["id"]
        assert payload["current_revision"]["revision_no"] == 2
        assert len(payload["revisions"]) == 2
        assert len(payload["current_revision"]["evidence_links"]) == 2
        assert payload["revisions"][0]["is_workspace_head"] is False
        assert payload["revisions"][1]["is_workspace_head"] is True

        detail = client.get(f"/kbs/{kb_id}/items/{item_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["current_revision"]["revision_no"] == 2


def test_invalid_manual_item_payload_returns_structured_contract_error():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Invalid Contract KB", "description": "invalid"}).json()["id"]

        created = client.post(
            f"/kbs/{kb_id}/items/manual",
            headers=headers,
            json={
                "title": "Invalid procedure",
                "statement": "Should fail contract",
                "item_type": "procedure",
                "structured_payload_json": {"steps": []},
            },
        )
        assert created.status_code == 400
        detail = created.json()["detail"]
        assert detail["item_type"] == "procedure"
        assert detail["errors"][0]["field"] == "steps"
