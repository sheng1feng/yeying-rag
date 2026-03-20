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


def test_legacy_retrieval_and_context_routes_are_removed():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        removed_requests = [
            ("POST", "/retrieval-context", {"session_id": "legacy", "query": "q", "kb_ids": [1]}),
            ("POST", "/retrieval/context", {"query": "q", "conversation": {"session_id": "legacy"}, "scope": {"kb_ids": [1]}, "policy": {}}),
            ("POST", "/retrieval/search", {"query": "q", "kb_ids": [1]}),
            ("POST", "/retrieval/retrieve", {"query": "q", "kb_ids": [1]}),
            ("POST", "/retrieval/assemble-context", {"query": "q"}),
            ("POST", "/retrieval/generate-context", {"query": "q", "kb_ids": [1], "session_id": "legacy"}),
            ("POST", "/retrieval/recall-memory", {"query": "q", "session_id": "legacy", "kb_ids": [1]}),
            ("POST", "/bot/retrieval-context", {"query": "q", "kb_ids": [1], "session_id": "legacy"}),
            ("POST", "/kbs/1/search", {"query": "q"}),
        ]

        for method, path, body in removed_requests:
            response = client.request(method, path, headers=headers, json=body)
            assert response.status_code == 404, f"{path} should be removed"
