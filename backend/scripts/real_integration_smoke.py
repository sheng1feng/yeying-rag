from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient


def warehouse_login(wallet_address: str, private_key: bytes, warehouse_base_url: str) -> str:
    challenge = httpx.post(
        f"{warehouse_base_url}/api/v1/public/auth/challenge",
        json={"address": wallet_address},
        timeout=30.0,
    )
    challenge.raise_for_status()
    message = challenge.json()["data"]["challenge"]
    signature = Account.sign_message(encode_defunct(text=message), private_key).signature.hex()
    verify = httpx.post(
        f"{warehouse_base_url}/api/v1/public/auth/verify",
        json={"address": wallet_address, "signature": signature},
        timeout=30.0,
    )
    verify.raise_for_status()
    return verify.json()["data"]["token"]


def warehouse_mkcol(warehouse_base_url: str, token: str, path: str) -> None:
    response = httpx.request(
        "MKCOL",
        f"{warehouse_base_url}/dav{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    if response.status_code not in (201, 405):
        response.raise_for_status()


def warehouse_put(warehouse_base_url: str, token: str, path: str, content: bytes) -> None:
    response = httpx.put(
        f"{warehouse_base_url}/dav{path}",
        headers={"Authorization": f"Bearer {token}"},
        content=content,
        timeout=60.0,
    )
    response.raise_for_status()


def warehouse_delete(warehouse_base_url: str, token: str, path: str) -> None:
    response = httpx.request(
        "DELETE",
        f"{warehouse_base_url}/dav{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    if response.status_code not in (200, 204, 404):
        response.raise_for_status()


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    temp_db = Path(tempfile.gettempdir()) / "knowledge_real_integration_smoke.db"
    if temp_db.exists():
        temp_db.unlink()

    os.environ["DATABASE_URL"] = f"sqlite:///{temp_db}"
    os.environ["VECTOR_STORE_MODE"] = "weaviate"

    from knowledge.core.settings import get_settings
    from knowledge.main import app
    from knowledge.services.vector_store import WeaviateVectorStore

    settings = get_settings()

    wallet = Account.create()
    wallet_address = wallet.address
    warehouse_access_token = warehouse_login(wallet_address, wallet.key, settings.warehouse_base_url)

    app_dir = f"/apps/knowledge-smoke-{uuid4().hex[:8]}"
    app_file = f"{app_dir}/notes.txt"
    personal_file = "/personal/uploads/knowledge-real-smoke.txt"

    warehouse_mkcol(settings.warehouse_base_url, warehouse_access_token, app_dir)
    warehouse_put(
        settings.warehouse_base_url,
        warehouse_access_token,
        app_file,
        b"App scoped warehouse data for knowledge import smoke test.",
    )

    with TestClient(app) as client:
        challenge = client.post("/auth/challenge", json={"wallet_address": wallet_address})
        challenge.raise_for_status()
        message = encode_defunct(text=challenge.json()["message"])
        signature = Account.sign_message(message, wallet.key)
        verify = client.post(
            "/auth/verify",
            json={"wallet_address": wallet_address, "signature": signature.signature.hex()},
        )
        verify.raise_for_status()
        headers = {"Authorization": f"Bearer {verify.json()['access_token']}"}

        bind_challenge = client.post("/warehouse/auth/challenge", headers=headers, json={"wallet_address": wallet_address})
        bind_challenge.raise_for_status()
        bind_message = encode_defunct(text=bind_challenge.json()["challenge"])
        bind_signature = Account.sign_message(bind_message, wallet.key)
        bind_verify = client.post(
            "/warehouse/auth/verify",
            headers=headers,
            json={"wallet_address": wallet_address, "signature": bind_signature.signature.hex()},
        )
        bind_verify.raise_for_status()

        kb = client.post(
            "/kbs",
            headers=headers,
            json={"name": f"Real Smoke KB {uuid4().hex[:6]}", "description": "real integration smoke"},
        )
        kb.raise_for_status()
        kb_id = kb.json()["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/uploads"},
            files={"file": ("knowledge-real-smoke.txt", b"Personal warehouse data for weaviate smoke test.", "text/plain")},
        )
        upload.raise_for_status()
        personal_path = upload.json()["warehouse_path"]

        preview = client.get(f"/warehouse/preview?path={personal_path}", headers=headers)
        preview.raise_for_status()

        for source_path in [personal_path, app_file]:
            bind = client.post(f"/kbs/{kb_id}/bindings", headers=headers, json={"source_path": source_path, "scope_type": "file"})
            bind.raise_for_status()
            task = client.post(f"/kbs/{kb_id}/tasks/import", headers=headers, json={"source_paths": [source_path]})
            task.raise_for_status()

        process = client.post("/tasks/process-pending", headers=headers)
        process.raise_for_status()

        stats = client.get(f"/kbs/{kb_id}/stats", headers=headers)
        stats.raise_for_status()

        long_memory = client.post(
            "/memory/long-term",
            headers=headers,
            json={"kb_id": kb_id, "category": "profile", "content": "user likes concise answers", "source": "smoke", "score": 90},
        )
        long_memory.raise_for_status()
        short_memory = client.post(
            "/memory/short-term",
            headers=headers,
            json={"session_id": "smoke-session", "memory_type": "summary", "content": "recent dialog about knowledge integration"},
        )
        short_memory.raise_for_status()

        search = client.post(f"/kbs/{kb_id}/search", headers=headers, json={"query": "weaviate smoke test"})
        search.raise_for_status()
        retrieval = client.post(
            "/retrieval-context",
            headers=headers,
            json={"session_id": "smoke-session", "kb_ids": [kb_id], "query": "concise answers and weaviate"},
        )
        retrieval.raise_for_status()

        task_list = client.get("/tasks", headers=headers)
        task_list.raise_for_status()

        print("knowledge stats:", stats.json())
        print("search hits:", search.json())
        print("retrieval context:", retrieval.json())

        delete_task = client.post(f"/kbs/{kb_id}/tasks/delete", headers=headers, json={"source_paths": [personal_path, app_file]})
        delete_task.raise_for_status()
        process_cleanup = client.post("/tasks/process-pending", headers=headers)
        process_cleanup.raise_for_status()

    store = WeaviateVectorStore()
    health = store.health()
    print("weaviate health:", health)
    client = store._connect()
    collection = client.collections.get(settings.weaviate_index_name)
    aggregate = collection.aggregate.over_all(total_count=True)
    print("collection total_count after cleanup:", aggregate.total_count)
    client.close()

    warehouse_delete(settings.warehouse_base_url, warehouse_access_token, personal_file)
    warehouse_delete(settings.warehouse_base_url, warehouse_access_token, app_file)
    warehouse_delete(settings.warehouse_base_url, warehouse_access_token, app_dir)


if __name__ == "__main__":
    main()
