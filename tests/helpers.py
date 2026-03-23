from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge.services.warehouse_scope import warehouse_app_root


def configure_warehouse_credentials(client: TestClient, headers: dict[str, str]) -> dict[str, int]:
    write_payload = {
        "key_id": "ak_test_write",
        "key_secret": "sk_test_write",
        "root_path": warehouse_app_root(),
    }
    write_response = client.post("/warehouse/credentials/write", headers=headers, json=write_payload)
    write_response.raise_for_status()

    read_payload = {
        "key_id": "ak_test_read",
        "key_secret": "sk_test_read",
        "root_path": warehouse_app_root(),
    }
    read_response = client.post("/warehouse/credentials/read", headers=headers, json=read_payload)
    read_response.raise_for_status()

    return {
        "write_credential_id": int(write_response.json()["id"]),
        "read_credential_id": int(read_response.json()["id"]),
    }
