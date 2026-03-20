from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from knowledge.main import app


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


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(tzinfo=None).isoformat()


def _create_manual_item(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    *,
    title: str,
    statement: str,
    item_type: str,
    payload: dict,
) -> tuple[int, int]:
    created = client.post(
        f"/kbs/{kb_id}/items/manual",
        headers=headers,
        json={
            "title": title,
            "statement": statement,
            "item_type": item_type,
            "structured_payload_json": payload,
        },
    )
    created.raise_for_status()
    body = created.json()
    return body["item"]["id"], body["current_revision"]["id"]


def _update_manual_item(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    item_id: int,
    *,
    statement: str,
    payload: dict,
) -> int:
    updated = client.patch(
        f"/kbs/{kb_id}/items/{item_id}",
        headers=headers,
        json={
            "statement": statement,
            "structured_payload_json": payload,
        },
    )
    updated.raise_for_status()
    return updated.json()["current_revision"]["id"]


def _publish_release(client: TestClient, headers: dict[str, str], kb_id: int, *, version: str, release_note: str) -> dict:
    response = client.post(
        f"/kbs/{kb_id}/releases",
        headers=headers,
        json={"version": version, "release_note": release_note},
    )
    response.raise_for_status()
    return response.json()


def _hotfix_release(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    base_release_id: int,
    *,
    version: str,
    release_note: str,
    knowledge_item_ids: list[int],
) -> dict:
    response = client.post(
        f"/kbs/{kb_id}/releases/{base_release_id}/hotfix",
        headers=headers,
        json={
            "version": version,
            "release_note": release_note,
            "knowledge_item_ids": knowledge_item_ids,
        },
    )
    response.raise_for_status()
    return response.json()


def _create_service_principal(
    client: TestClient,
    headers: dict[str, str],
    *,
    service_id: str,
    display_name: str,
) -> tuple[dict, str]:
    response = client.post(
        "/service-principals",
        headers=headers,
        json={
            "service_id": service_id,
            "display_name": display_name,
            "identity_type": "api_key",
        },
    )
    response.raise_for_status()
    payload = response.json()
    assert "principal" in payload
    assert "api_key" in payload
    return payload["principal"], payload["api_key"]


def _create_grant(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    *,
    service_principal_id: int,
    release_selection_mode: str,
    default_result_mode: str = "compact",
    pinned_release_id: int | None = None,
    expires_at: str | None = None,
) -> dict:
    body = {
        "service_principal_id": service_principal_id,
        "release_selection_mode": release_selection_mode,
        "default_result_mode": default_result_mode,
    }
    if pinned_release_id is not None:
        body["pinned_release_id"] = pinned_release_id
    if expires_at is not None:
        body["expires_at"] = expires_at
    response = client.post(f"/kbs/{kb_id}/grants", headers=headers, json=body)
    response.raise_for_status()
    return response.json()


def test_api_key_service_principal_is_returned_once_and_can_be_verified():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        principal, raw_api_key = _create_service_principal(
            client,
            headers,
            service_id=f"svc-{uuid4().hex[:8]}",
            display_name="Search Service",
        )
        assert principal["identity_type"] == "api_key"
        assert principal["principal_status"] == "active"
        assert raw_api_key

        listed = client.get("/service-principals", headers=headers)
        listed.raise_for_status()
        listed_payload = listed.json()
        assert any(item["id"] == principal["id"] for item in listed_payload)
        assert all("api_key" not in item for item in listed_payload)

        verified = client.post("/service-principals/verify", headers=headers, json={"api_key": raw_api_key})
        verified.raise_for_status()
        verified_payload = verified.json()
        assert verified_payload["principal"]["id"] == principal["id"]
        assert verified_payload["principal"]["service_id"] == principal["service_id"]
        assert "api_key" not in verified_payload["principal"]

        disabled = client.patch(
            f"/service-principals/{principal['id']}",
            headers=headers,
            json={"principal_status": "disabled"},
        )
        disabled.raise_for_status()
        assert disabled.json()["principal_status"] == "disabled"

        verify_disabled = client.post("/service-principals/verify", headers=headers, json={"api_key": raw_api_key})
        assert verify_disabled.status_code >= 400


def test_service_grants_support_latest_published_pinned_release_and_lifecycle_states():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Grant KB", "description": "grant"}).json()["id"]

        item_id, revision_v1 = _create_manual_item(
            client,
            headers,
            kb_id,
            title="Grant fact",
            statement="Release one statement.",
            item_type="fact",
            payload={"fact": "Release one statement."},
        )
        release_1 = _publish_release(client, headers, kb_id, version=f"release-{uuid4().hex[:6]}", release_note="baseline")
        release_1_id = release_1["release"]["id"]

        revision_v2 = _update_manual_item(
            client,
            headers,
            kb_id,
            item_id,
            statement="Release two statement.",
            payload={"fact": "Release two statement."},
        )
        release_2 = _hotfix_release(
            client,
            headers,
            kb_id,
            release_1_id,
            version=f"release-{uuid4().hex[:6]}",
            release_note="hotfix",
            knowledge_item_ids=[item_id],
        )
        release_2_id = release_2["release"]["id"]
        assert release_1_id != release_2_id
        assert release_1["items"][0]["knowledge_item_revision_id"] == revision_v1
        assert release_2["items"][0]["knowledge_item_revision_id"] == revision_v2

        latest_principal, latest_api_key = _create_service_principal(
            client,
            headers,
            service_id=f"svc-{uuid4().hex[:8]}",
            display_name="Latest Service",
        )
        pinned_principal, pinned_api_key = _create_service_principal(
            client,
            headers,
            service_id=f"svc-{uuid4().hex[:8]}",
            display_name="Pinned Service",
        )
        expired_principal, _ = _create_service_principal(
            client,
            headers,
            service_id=f"svc-{uuid4().hex[:8]}",
            display_name="Expired Service",
        )

        latest_grant = _create_grant(
            client,
            headers,
            kb_id,
            service_principal_id=latest_principal["id"],
            release_selection_mode="latest_published",
        )
        pinned_grant = _create_grant(
            client,
            headers,
            kb_id,
            service_principal_id=pinned_principal["id"],
            release_selection_mode="pinned_release",
            pinned_release_id=release_1_id,
        )
        expired_grant = _create_grant(
            client,
            headers,
            kb_id,
            service_principal_id=expired_principal["id"],
            release_selection_mode="latest_published",
            expires_at=_iso_utc(datetime.now(UTC) - timedelta(days=1)),
        )

        listed = client.get(f"/kbs/{kb_id}/grants", headers=headers)
        listed.raise_for_status()
        grants = {item["id"]: item for item in listed.json()}
        assert grants[latest_grant["id"]]["release_selection_mode"] == "latest_published"
        assert grants[pinned_grant["id"]]["release_selection_mode"] == "pinned_release"
        assert grants[pinned_grant["id"]]["pinned_release_id"] == release_1_id
        assert grants[expired_grant["id"]]["grant_status"] == "expired"

        suspended = client.patch(
            f"/kbs/{kb_id}/grants/{latest_grant['id']}",
            headers=headers,
            json={"grant_status": "suspended"},
        )
        suspended.raise_for_status()
        assert suspended.json()["grant_status"] == "suspended"

        revoked = client.patch(
            f"/kbs/{kb_id}/grants/{pinned_grant['id']}",
            headers=headers,
            json={"grant_status": "revoked", "revoked_by": account.address},
        )
        revoked.raise_for_status()
        revoked_payload = revoked.json()
        assert revoked_payload["grant_status"] == "revoked"
        assert str(revoked_payload["revoked_by"]).lower() == account.address.lower()
        assert revoked_payload["revoked_at"] is not None

        verify_latest = client.post("/service-principals/verify", headers=headers, json={"api_key": latest_api_key})
        verify_latest.raise_for_status()
        assert verify_latest.json()["principal"]["id"] == latest_principal["id"]

        verify_pinned = client.post("/service-principals/verify", headers=headers, json={"api_key": pinned_api_key})
        verify_pinned.raise_for_status()
        assert verify_pinned.json()["principal"]["id"] == pinned_principal["id"]


def test_pinned_release_remains_stable_after_current_release_changes():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Pinned KB", "description": "pinned"}).json()["id"]

        item_id, revision_v1 = _create_manual_item(
            client,
            headers,
            kb_id,
            title="Pinned item",
            statement="Version one statement.",
            item_type="fact",
            payload={"fact": "Version one statement."},
        )
        release_1 = _publish_release(client, headers, kb_id, version=f"release-{uuid4().hex[:6]}", release_note="baseline")
        release_1_id = release_1["release"]["id"]

        pinned_principal, pinned_api_key = _create_service_principal(
            client,
            headers,
            service_id=f"svc-{uuid4().hex[:8]}",
            display_name="Pinned Read Service",
        )
        pinned_grant = _create_grant(
            client,
            headers,
            kb_id,
            service_principal_id=pinned_principal["id"],
            release_selection_mode="pinned_release",
            pinned_release_id=release_1_id,
        )
        assert pinned_grant["pinned_release_id"] == release_1_id

        revision_v2 = _update_manual_item(
            client,
            headers,
            kb_id,
            item_id,
            statement="Version two statement.",
            payload={"fact": "Version two statement."},
        )
        release_2 = _hotfix_release(
            client,
            headers,
            kb_id,
            release_1_id,
            version=f"release-{uuid4().hex[:6]}",
            release_note="advance current release",
            knowledge_item_ids=[item_id],
        )
        assert release_2["items"][0]["knowledge_item_revision_id"] == revision_v2
        assert release_1["items"][0]["knowledge_item_revision_id"] == revision_v1

        listed = client.get(f"/kbs/{kb_id}/grants", headers=headers)
        listed.raise_for_status()
        listed_payload = {item["id"]: item for item in listed.json()}
        assert listed_payload[pinned_grant["id"]]["pinned_release_id"] == release_1_id
        assert listed_payload[pinned_grant["id"]]["release_selection_mode"] == "pinned_release"

        service_grants = client.get("/service/grants", headers={"X-Service-Api-Key": pinned_api_key})
        service_kbs = client.get("/service/kbs", headers={"X-Service-Api-Key": pinned_api_key})
        if service_grants.status_code == 404 or service_kbs.status_code == 404:
            pytest.skip("service introspection routes not implemented")
        service_grants.raise_for_status()
        service_kbs.raise_for_status()
        grants_payload = service_grants.json()
        assert any(item["kb_id"] == kb_id and item["pinned_release_id"] == release_1_id for item in grants_payload)
        kbs_payload = service_kbs.json()
        assert any(item["kb_id"] == kb_id for item in kbs_payload)
