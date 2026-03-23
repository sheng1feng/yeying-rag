from __future__ import annotations

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


def _rollback_release(
    client: TestClient,
    headers: dict[str, str],
    kb_id: int,
    target_release_id: int,
    *,
    version: str,
    release_note: str,
) -> dict:
    response = client.post(
        f"/kbs/{kb_id}/releases/{target_release_id}/rollback",
        headers=headers,
        json={"version": version, "release_note": release_note},
    )
    response.raise_for_status()
    return response.json()


def _get_current_release(client: TestClient, headers: dict[str, str], kb_id: int) -> dict:
    response = client.get(f"/kbs/{kb_id}/releases/current", headers=headers)
    response.raise_for_status()
    return response.json()


def _get_release_detail(client: TestClient, headers: dict[str, str], kb_id: int, release_id: int) -> dict:
    response = client.get(f"/kbs/{kb_id}/releases/{release_id}", headers=headers)
    response.raise_for_status()
    return response.json()


def _list_releases(client: TestClient, headers: dict[str, str], kb_id: int) -> list[dict]:
    response = client.get(f"/kbs/{kb_id}/releases", headers=headers)
    response.raise_for_status()
    return response.json()


def _release_item_revision_map(release_detail: dict) -> dict[int, int]:
    return {
        int(item["knowledge_item_id"]): int(item["knowledge_item_revision_id"])
        for item in (release_detail.get("items") or [])
    }


def test_publish_release_lists_history_and_current_effective_release():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Release KB", "description": "release"}).json()["id"]

        item_a_id, revision_a_v1 = _create_manual_item(
            client,
            headers,
            kb_id,
            title="Published fact",
            statement="Published release is the only truth.",
            item_type="fact",
            payload={"fact": "Published release is the only truth."},
        )
        item_b_id, revision_b_v1 = _create_manual_item(
            client,
            headers,
            kb_id,
            title="Published rule",
            statement="Services must read published views.",
            item_type="rule",
            payload={"rule": "Services must read published views."},
        )

        published = _publish_release(client, headers, kb_id, version="release-1", release_note="initial publish")
        assert published["release"]["status"] == "published"
        assert published["release"]["version"] == "release-1"
        assert _release_item_revision_map(published) == {
            item_a_id: revision_a_v1,
            item_b_id: revision_b_v1,
        }

        current = _get_current_release(client, headers, kb_id)
        assert current["release"]["id"] == published["release"]["id"]
        assert current["release"]["status"] == "published"
        assert _release_item_revision_map(current) == {
            item_a_id: revision_a_v1,
            item_b_id: revision_b_v1,
        }

        history = _list_releases(client, headers, kb_id)
        assert len(history) == 1
        assert history[0]["id"] == published["release"]["id"]
        assert history[0]["status"] == "published"

        detail = _get_release_detail(client, headers, kb_id, published["release"]["id"])
        assert detail["release"]["version"] == "release-1"
        assert _release_item_revision_map(detail) == {
            item_a_id: revision_a_v1,
            item_b_id: revision_b_v1,
        }


def test_workspace_revision_changes_do_not_automatically_pollute_published_release():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Workspace KB", "description": "workspace"}).json()["id"]

        item_id, revision_v1 = _create_manual_item(
            client,
            headers,
            kb_id,
            title="Workspace rule",
            statement="Always use current published release.",
            item_type="rule",
            payload={"rule": "Always use current published release."},
        )

        release_1 = _publish_release(client, headers, kb_id, version="release-1", release_note="baseline")
        assert _release_item_revision_map(release_1) == {item_id: revision_v1}

        revision_v2 = _update_manual_item(
            client,
            headers,
            kb_id,
            item_id,
            statement="Always use current published release by default.",
            payload={"rule": "Always use current published release by default."},
        )
        assert revision_v2 != revision_v1

        current = _get_current_release(client, headers, kb_id)
        assert _release_item_revision_map(current) == {item_id: revision_v1}

        item_detail = client.get(f"/kbs/{kb_id}/items/{item_id}", headers=headers)
        item_detail.raise_for_status()
        assert item_detail.json()["current_revision"]["id"] == revision_v2


def test_hotfix_creates_new_release_that_reuses_previous_snapshot_except_selected_items():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Hotfix KB", "description": "hotfix"}).json()["id"]

        item_a_id, revision_a_v1 = _create_manual_item(
            client,
            headers,
            kb_id,
            title="Hotfix item A",
            statement="Published release is primary.",
            item_type="fact",
            payload={"fact": "Published release is primary."},
        )
        item_b_id, revision_b_v1 = _create_manual_item(
            client,
            headers,
            kb_id,
            title="Hotfix item B",
            statement="Services use published view.",
            item_type="rule",
            payload={"rule": "Services use published view."},
        )

        release_1 = _publish_release(client, headers, kb_id, version="release-1", release_note="baseline")
        release_1_id = release_1["release"]["id"]
        assert _release_item_revision_map(release_1) == {
            item_a_id: revision_a_v1,
            item_b_id: revision_b_v1,
        }

        revision_a_v2 = _update_manual_item(
            client,
            headers,
            kb_id,
            item_a_id,
            statement="Published release is primary truth.",
            payload={"fact": "Published release is primary truth."},
        )

        before_hotfix_current = _get_current_release(client, headers, kb_id)
        assert _release_item_revision_map(before_hotfix_current) == {
            item_a_id: revision_a_v1,
            item_b_id: revision_b_v1,
        }

        hotfix = _hotfix_release(
            client,
            headers,
            kb_id,
            release_1_id,
            version="release-1.1",
            release_note="hotfix item A",
            knowledge_item_ids=[item_a_id],
        )
        assert hotfix["release"]["version"] == "release-1.1"
        assert hotfix["release"]["status"] == "published"
        assert hotfix["release"]["id"] != release_1_id
        assert hotfix["release"]["supersedes_release_id"] == release_1_id
        assert _release_item_revision_map(hotfix) == {
            item_a_id: revision_a_v2,
            item_b_id: revision_b_v1,
        }

        release_1_detail = _get_release_detail(client, headers, kb_id, release_1_id)
        assert _release_item_revision_map(release_1_detail) == {
            item_a_id: revision_a_v1,
            item_b_id: revision_b_v1,
        }

        current = _get_current_release(client, headers, kb_id)
        assert current["release"]["id"] == hotfix["release"]["id"]
        assert _release_item_revision_map(current) == {
            item_a_id: revision_a_v2,
            item_b_id: revision_b_v1,
        }


def test_rollback_restores_target_release_snapshot_as_new_current_release():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id = client.post("/kbs", headers=headers, json={"name": "Rollback KB", "description": "rollback"}).json()["id"]

        item_id, revision_v1 = _create_manual_item(
            client,
            headers,
            kb_id,
            title="Rollback item",
            statement="Version one statement.",
            item_type="fact",
            payload={"fact": "Version one statement."},
        )
        release_1 = _publish_release(client, headers, kb_id, version="release-1", release_note="baseline")

        revision_v2 = _update_manual_item(
            client,
            headers,
            kb_id,
            item_id,
            statement="Version two statement.",
            payload={"fact": "Version two statement."},
        )
        hotfix = _hotfix_release(
            client,
            headers,
            kb_id,
            release_1["release"]["id"],
            version="release-2",
            release_note="promote v2",
            knowledge_item_ids=[item_id],
        )
        assert _release_item_revision_map(hotfix) == {item_id: revision_v2}

        rollback = _rollback_release(
            client,
            headers,
            kb_id,
            release_1["release"]["id"],
            version="release-3",
            release_note="rollback to baseline",
        )
        assert rollback["release"]["status"] == "published"
        assert rollback["release"]["id"] not in {release_1["release"]["id"], hotfix["release"]["id"]}
        assert _release_item_revision_map(rollback) == {item_id: revision_v1}

        current = _get_current_release(client, headers, kb_id)
        assert current["release"]["id"] == rollback["release"]["id"]
        assert _release_item_revision_map(current) == {item_id: revision_v1}

        history = _list_releases(client, headers, kb_id)
        history_by_id = {item["id"]: item for item in history}
        assert history_by_id[hotfix["release"]["id"]]["status"] == "rolled_back"
        assert history_by_id[release_1["release"]["id"]]["status"] in {"published", "superseded"}
