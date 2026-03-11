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


def test_warehouse_ucan_bind_and_status():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        bootstrap = client.post("/warehouse/auth/ucan/bootstrap", headers=headers, json={"wallet_address": account.address})
        assert bootstrap.status_code == 200
        payload = bootstrap.json()
        assert payload["nonce"]
        assert payload["message"]

        signed = Account.sign_message(encode_defunct(text=payload["message"]), account.key)
        verify = client.post(
            "/warehouse/auth/ucan/verify",
            headers=headers,
            json={
                "wallet_address": account.address,
                "nonce": payload["nonce"],
                "signature": signed.signature.hex(),
            },
        )
        assert verify.status_code == 200
        assert verify.json()["binding_type"] == "ucan"

        status = client.get("/warehouse/auth/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["bound"] is True
        assert status.json()["binding_type"] == "ucan"

        browse = client.get("/warehouse/browse?path=/personal", headers=headers)
        assert browse.status_code == 200


def test_warehouse_app_ucan_bind_status():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        jwt_challenge = client.post("/warehouse/auth/challenge", headers=headers, json={"wallet_address": account.address})
        assert jwt_challenge.status_code == 200
        jwt_signed = Account.sign_message(encode_defunct(text=jwt_challenge.json()["challenge"]), account.key)
        jwt_verify = client.post(
            "/warehouse/auth/verify",
            headers=headers,
            json={"wallet_address": account.address, "signature": jwt_signed.signature.hex()},
        )
        assert jwt_verify.status_code == 200

        bootstrap = client.post(
            "/warehouse/auth/apps/ucan/bootstrap",
            headers=headers,
            json={"wallet_address": account.address, "app_id": "knowledge-smoke-app", "action": "read"},
        )
        assert bootstrap.status_code == 200
        payload = bootstrap.json()

        signed = Account.sign_message(encode_defunct(text=payload["message"]), account.key)
        verify = client.post(
            "/warehouse/auth/apps/ucan/verify",
            headers=headers,
            json={
                "wallet_address": account.address,
                "app_id": "knowledge-smoke-app",
                "nonce": payload["nonce"],
                "signature": signed.signature.hex(),
            },
        )
        assert verify.status_code == 200
        assert "knowledge-smoke-app" in verify.json()["app_ucan_apps"]
