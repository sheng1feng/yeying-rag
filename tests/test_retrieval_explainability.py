from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from knowledge.db.session import session_scope
from knowledge.main import app
from knowledge.models import ImportTask
from knowledge.utils.time import utc_now


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


def test_retrieve_debug_explains_filtered_empty_results_and_provider_mode():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id, _ = _create_indexed_kb(client, headers, "Explain Empty", b"knowledge evidence for explainability")

        response = client.post(
            "/retrieval/retrieve",
            headers=headers,
            json={
                "query": "knowledge evidence",
                "kb_ids": [kb_id],
                "source_scope": ["/personal/uploads/missing.txt"],
                "debug": True,
            },
        )
        response.raise_for_status()
        payload = response.json()
        assert payload["knowledge_hits"] == []
        assert payload["source_refs"] == []
        assert payload["debug"]["empty_reasons"] == ["no_knowledge_hits_after_source_scope"]
        assert payload["debug"]["scope_effects"]["knowledge"]["source_scope"] == ["/personal/uploads/missing.txt"]
        assert payload["debug"]["provider"]["vector_store_mode"] == "db"
        assert payload["debug"]["provider"]["embedding_provider"] == "mock"


def test_memory_recall_debug_explains_namespace_scope_miss():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id, _ = _create_indexed_kb(client, headers, "Explain Memory", b"memory debug baseline")

        response = client.post(
            "/retrieval/recall-memory",
            headers=headers,
            json={
                "query": "preference",
                "session_id": "debug-session",
                "memory_namespace": "robot-missing",
                "kb_ids": [kb_id],
                "debug": True,
            },
        )
        response.raise_for_status()
        payload = response.json()
        assert payload["short_term_hits"] == []
        assert "no_short_term_memories_in_namespace" in payload["debug"]["empty_reasons"]
        assert "no_long_term_memories_in_scope" in payload["debug"]["empty_reasons"]
        assert payload["debug"]["scope_effects"]["memory"]["memory_namespace"] == "robot-missing"


def test_context_assembly_debug_explains_budget_truncation():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/retrieval/assemble-context",
            headers=headers,
            json={
                "query": "budget explainability",
                "knowledge_hits": [
                    {
                        "chunk_id": index,
                        "kb_id": 1,
                        "document_id": index,
                        "source_path": f"/source/{index}.txt",
                        "text": "budget heavy evidence " * 30,
                        "score": 0.9,
                        "metadata": {},
                    }
                    for index in range(1, 4)
                ],
                "retrieval_policy": {"max_context_chars": 120},
                "debug": True,
            },
        )
        response.raise_for_status()
        payload = response.json()
        assert payload["applied_policy"]["max_context_chars"] == 400
        assert payload["debug"]["budget_effects"]["requested_max_context_chars"] == 120
        assert payload["debug"]["budget_effects"]["applied_max_context_chars"] == 400
        assert "knowledge" in payload["debug"]["budget_effects"]["truncated_sections"]
        assert payload["debug"]["scope_effects"]["context"]["source_refs_from_knowledge_hits"] == payload["source_refs"]


def test_trace_links_retrieval_memory_ingest_and_ops_failures():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb_id, source_path = _create_indexed_kb(client, headers, "Trace Link", b"trace link knowledge evidence")

        context = client.post(
            "/retrieval/context",
            headers=headers,
            json={
                "query": "knowledge evidence",
                "conversation": {"session_id": "trace-session"},
                "scope": {"kb_ids": [kb_id], "source_scope": [source_path]},
            },
        )
        context.raise_for_status()
        trace_id = context.json()["trace"]["trace_id"]

        ingest = client.post(
            "/memory/ingest",
            headers=headers,
            json={
                "session_id": "trace-session",
                "kb_id": kb_id,
                "query": "keep trace linked",
                "answer": "trace linked answer",
                "source": "bot",
                "trace_id": trace_id,
                "source_refs": context.json()["knowledge"]["source_refs"],
            },
        )
        ingest.raise_for_status()

        events = client.get(f"/memory/ingestions?trace_id={trace_id}", headers=headers)
        events.raise_for_status()
        event_payload = events.json()
        assert len(event_payload) == 1
        assert event_payload[0]["trace_id"] == trace_id

        with session_scope() as db:
            db.add(
                ImportTask(
                    owner_wallet_address=account.address,
                    kb_id=kb_id,
                    task_type="import",
                    status="failed",
                    source_paths=[source_path],
                    stats_json={"trace_id": trace_id, "operation": "trace-debug"},
                    error_message="trace linked failure",
                    finished_at=utc_now(),
                )
            )

        failures = client.get(f"/ops/tasks/failures?trace_id={trace_id}", headers=headers)
        failures.raise_for_status()
        failure_payload = failures.json()
        assert len(failure_payload) == 1
        assert failure_payload[0]["trace_id"] == trace_id
        assert failure_payload[0]["stats_json"]["operation"] == "trace-debug"


def test_legacy_and_standard_context_keep_empty_result_semantics_aligned():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        kb = client.post("/kbs", headers=headers, json={"name": "Empty Compat", "description": "empty"}).json()
        kb_id = kb["id"]

        legacy = client.post(
            "/retrieval-context",
            headers=headers,
            json={"session_id": "empty-session", "query": "nothing here", "kb_ids": [kb_id], "top_k": 2},
        )
        legacy.raise_for_status()

        standard = client.post(
            "/retrieval/context",
            headers=headers,
            json={
                "query": "nothing here",
                "conversation": {"session_id": "empty-session"},
                "scope": {"kb_ids": [kb_id]},
                "policy": {"top_k": 2},
                "debug": True,
            },
        )
        standard.raise_for_status()

        legacy_payload = legacy.json()
        standard_payload = standard.json()
        assert legacy_payload["kb_blocks"] == []
        assert standard_payload["knowledge"]["hits"] == []
        assert legacy_payload["source_refs"] == standard_payload["knowledge"]["source_refs"] == []
        assert legacy_payload["scores"]["applied_policy"]["top_k"] == standard_payload["trace"]["applied_policy"]["top_k"]
        assert "no_knowledge_hits_found" in standard_payload["debug"]["empty_reasons"]
