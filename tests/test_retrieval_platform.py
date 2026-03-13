from __future__ import annotations

from types import SimpleNamespace

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from knowledge.api import routes_ops
from knowledge.main import app
from knowledge.services.context_assembly import ContextAssemblyExecutor


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


def _create_indexed_kb(client: TestClient, headers: dict[str, str], name: str, content: bytes) -> tuple[int, str]:
    kb = client.post("/kbs", headers=headers, json={"name": name, "description": name}).json()
    kb_id = kb["id"]
    upload = client.post(
        "/warehouse/upload",
        headers=headers,
        data={"target_dir": "/personal/uploads"},
        files={"file": (f"{name}.txt", content, "text/plain")},
    )
    upload.raise_for_status()
    source_path = upload.json()["warehouse_path"]

    bind = client.post(
        f"/kbs/{kb_id}/bindings",
        headers=headers,
        json={"source_path": source_path, "scope_type": "file"},
    )
    bind.raise_for_status()

    task = client.post(
        f"/kbs/{kb_id}/tasks/import",
        headers=headers,
        json={"source_paths": [source_path]},
    )
    task.raise_for_status()

    processed = client.post("/tasks/process-pending", headers=headers)
    processed.raise_for_status()
    assert processed.json()["processed"] >= 1
    return kb_id, source_path


def test_search_and_retrieve_keep_distinct_contracts():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id, source_path = _create_indexed_kb(
            client,
            headers,
            "Layered Search",
            b"layered retrieval contract knowledge evidence",
        )

        search = client.post(
            "/retrieval/search",
            headers=headers,
            json={"query": "knowledge evidence", "kb_ids": [kb_id], "source_scope": [source_path], "debug": True},
        )
        search.raise_for_status()
        search_payload = search.json()
        assert search_payload["hits"]
        assert "source_refs" not in search_payload
        assert "applied_policy" not in search_payload
        assert search_payload["debug"]["search_filters"]["source_paths"] == [source_path]

        retrieve = client.post(
            "/retrieval/retrieve",
            headers=headers,
            json={
                "query": "knowledge evidence",
                "kb_ids": [kb_id],
                "source_scope": [source_path],
                "retrieval_policy": {"top_k": 2},
                "debug": True,
            },
        )
        retrieve.raise_for_status()
        retrieve_payload = retrieve.json()
        assert retrieve_payload["knowledge_hits"]
        assert retrieve_payload["source_refs"] == [source_path]
        assert retrieve_payload["applied_policy"]["top_k"] == 2
        assert "hits" not in retrieve_payload
        assert "short_term_hits" not in retrieve_payload


def test_bot_and_neutral_context_share_memory_namespace_isolation():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id, _ = _create_indexed_kb(
            client,
            headers,
            "Namespace Platform",
            b"context route isolation baseline",
        )

        for namespace, answer in [("robot-a", "A: 简洁"), ("robot-b", "B: 详细")]:
            ingest = client.post(
                "/memory/ingest",
                headers=headers,
                json={
                    "session_id": "platform-shared",
                    "memory_namespace": namespace,
                    "kb_id": kb_id,
                    "query": f"{namespace} 偏好",
                    "answer": answer,
                    "source": "bot",
                },
            )
            ingest.raise_for_status()

        bot_context = client.post(
            "/bot/retrieval-context",
            headers=headers,
            json={
                "query": "偏好",
                "kb_ids": [kb_id],
                "session_id": "platform-shared",
                "memory_namespace": "robot-a",
                "app_id": "bot-platform",
            },
        )
        bot_context.raise_for_status()
        bot_payload = bot_context.json()
        assert bot_payload["short_term_memory_hits"]
        assert all(item["metadata"]["memory_namespace"] == "robot-a" for item in bot_payload["short_term_memory_hits"])
        assert any("A: 简洁" in item["content"] for item in bot_payload["short_term_memory_hits"])
        assert not any("B: 详细" in item["content"] for item in bot_payload["short_term_memory_hits"])

        neutral_context = client.post(
            "/retrieval/context",
            headers=headers,
            json={
                "query": "偏好",
                "conversation": {"session_id": "platform-shared", "memory_namespace": "robot-b"},
                "scope": {"kb_ids": [kb_id]},
            },
        )
        neutral_context.raise_for_status()
        neutral_payload = neutral_context.json()
        assert neutral_payload["memory"]["short_term_hits"]
        assert all(item["metadata"]["memory_namespace"] == "robot-b" for item in neutral_payload["memory"]["short_term_hits"])
        assert any("B: 详细" in item["content"] for item in neutral_payload["memory"]["short_term_hits"])
        assert not any("A: 简洁" in item["content"] for item in neutral_payload["memory"]["short_term_hits"])


def test_retrieval_isolated_by_wallet_across_search_and_memory():
    account_a = Account.create()
    account_b = Account.create()
    with TestClient(app) as client:
        headers_a = {"Authorization": f"Bearer {_login(client, account_a)}"}
        headers_b = {"Authorization": f"Bearer {_login(client, account_b)}"}

        kb_a_id, _ = _create_indexed_kb(
            client,
            headers_a,
            "Wallet Alpha",
            b"alpha wallet private knowledge evidence",
        )
        memory = client.post(
            "/memory/short-term",
            headers=headers_a,
            json={"session_id": "wallet-shared", "memory_type": "summary", "content": "alpha private memory"},
        )
        memory.raise_for_status()

        forbidden_search = client.post(
            "/retrieval/search",
            headers=headers_b,
            json={"query": "alpha", "kb_ids": [kb_a_id]},
        )
        assert forbidden_search.status_code == 404

        recall = client.post(
            "/retrieval/recall-memory",
            headers=headers_b,
            json={"query": "alpha", "session_id": "wallet-shared", "retrieval_policy": {"memory_top_k": 5}},
        )
        recall.raise_for_status()
        assert recall.json()["short_term_hits"] == []
        assert recall.json()["long_term_hits"] == []


def test_ops_stores_health_reports_provider_modes(monkeypatch):
    account = Account.create()

    class FakeVectorStore:
        def health(self) -> dict:
            return {"backend": "weaviate", "ready": True, "live": True}

    fake_settings = SimpleNamespace(
        vector_store_mode="weaviate",
        model_provider_mode="openai_compatible",
        model_gateway_base_url="https://gateway.example.com",
        warehouse_gateway_mode="bound_token",
        warehouse_base_url="https://warehouse.example.com",
    )

    monkeypatch.setattr(routes_ops, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(routes_ops, "build_vector_store", lambda: FakeVectorStore())

    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/ops/stores/health", headers=headers)
        response.raise_for_status()
        payload = response.json()
        assert payload["vector_store_mode"] == "weaviate"
        assert payload["vector_store_status"]["backend"] == "weaviate"
        assert payload["model_provider_mode"] == "openai_compatible"
        assert payload["model_provider_status"] == "configured"
        assert payload["warehouse_gateway_mode"] == "bound_token"


def test_context_assembly_handles_large_hitsets_stably():
    executor = ContextAssemblyExecutor(settings=SimpleNamespace(retrieval_top_k=6, memory_top_k=4))
    knowledge_hits = [
        {
            "chunk_id": index,
            "kb_id": 1,
            "document_id": index,
            "source_path": f"/bulk/doc-{index}.txt",
            "text": f"evidence block {index} " * 12,
            "score": 1.0 - index / 1000.0,
            "metadata": {},
        }
        for index in range(1, 121)
    ]
    result = executor.assemble(
        query="bulk evidence",
        knowledge_hits=knowledge_hits,
        retrieval_policy={"max_context_chars": 900},
    )
    assert result["context_sections"]
    assert result["applied_policy"]["max_context_chars"] == 900
    assert len(result["assembled_context"]) <= 960
    assert result["context_sections"][0]["truncated"] is True
    assert result["context_sections"][0]["item_count"] < len(knowledge_hits)
