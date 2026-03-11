from __future__ import annotations

import fcntl
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from knowledge.db.session import engine
from knowledge.main import app
from knowledge.workers.runner import Worker


def _login(client: TestClient, account) -> str:
    challenge = client.post("/auth/challenge", json={"wallet_address": account.address}).json()
    message = encode_defunct(text=challenge["message"])
    signed = Account.sign_message(message, account.key)
    verify = client.post(
        "/auth/verify",
        json={"wallet_address": account.address, "signature": signed.signature.hex()},
    )
    data = verify.json()
    return data["access_token"]


def test_end_to_end_auth_upload_import_search():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Personal KB", "description": "demo"}).json()
        kb_id = kb["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/uploads"},
            files={"file": ("hello.txt", b"hello warehouse and knowledge search", "text/plain")},
        )
        assert upload.status_code == 200
        upload_data = upload.json()
        assert upload_data["warehouse_path"].startswith("/personal/")

        uploads = client.get("/warehouse/uploads", headers=headers)
        assert uploads.status_code == 200
        assert any(item["warehouse_target_path"] == upload_data["warehouse_path"] for item in uploads.json())

        preview = client.get(f"/warehouse/preview?path={upload_data['warehouse_path']}", headers=headers)
        assert preview.status_code == 200
        assert "warehouse and knowledge search" in preview.json()["preview"]

        bind = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": upload_data["warehouse_path"], "scope_type": "file"},
        )
        assert bind.status_code == 200

        stats = client.get(f"/kbs/{kb_id}/stats", headers=headers)
        assert stats.status_code == 200
        assert stats.json()["bindings_count"] >= 1

        task = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": [upload_data["warehouse_path"]]},
        )
        assert task.status_code == 200

        processed = client.post("/tasks/process-pending", headers=headers)
        assert processed.status_code == 200
        assert processed.json()["processed"] >= 1

        task_items = client.get(f"/tasks/{task.json()['id']}/items", headers=headers)
        assert task_items.status_code == 200
        assert any(item["status"] == "indexed" for item in task_items.json())

        retry = client.post(f"/tasks/{task.json()['id']}/retry", headers=headers)
        assert retry.status_code == 200
        assert retry.json()["task_type"] == "import"

        second_task = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": [upload_data["warehouse_path"]]},
        )
        assert second_task.status_code == 200
        processed_second = client.post("/tasks/process-pending", headers=headers)
        assert processed_second.status_code == 200
        second_items = client.get(f"/tasks/{second_task.json()['id']}/items", headers=headers)
        assert second_items.status_code == 200
        assert any(item["status"] == "skipped" for item in second_items.json())

        search = client.post(f"/kbs/{kb_id}/search", headers=headers, json={"query": "warehouse knowledge"})
        assert search.status_code == 200
        assert len(search.json()) >= 1

        retrieval = client.post(
            "/retrieval-context",
            headers=headers,
            json={"session_id": "s1", "query": "knowledge", "kb_ids": [kb_id]},
        )
        assert retrieval.status_code == 200
        body = retrieval.json()
        assert "kb_blocks" in body
        assert len(body["kb_blocks"]) >= 1

        ops_overview = client.get("/ops/overview")
        assert ops_overview.status_code == 200
        assert ops_overview.json()["knowledge_bases"] >= 1

        ops_workers = client.get("/ops/workers")
        assert ops_workers.status_code == 200
        assert len(ops_workers.json()) >= 1


def test_memory_crud():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        created = client.post(
            "/memory/long-term",
            headers=headers,
            json={"content": "user prefers concise answers", "category": "preference", "source": "bot", "score": 90},
        )
        assert created.status_code == 200
        memory_id = created.json()["id"]

        listed = client.get("/memory/long-term", headers=headers)
        assert listed.status_code == 200
        assert any(item["id"] == memory_id for item in listed.json())

        deleted = client.delete(f"/memory/long-term/{memory_id}", headers=headers)
        assert deleted.status_code == 200

        short_created = client.post(
            "/memory/short-term",
            headers=headers,
            json={"session_id": "memory-crud", "memory_type": "summary", "content": "short lived summary"},
        )
        assert short_created.status_code == 200
        short_id = short_created.json()["id"]

        short_deleted = client.delete(f"/memory/short-term/{short_id}", headers=headers)
        assert short_deleted.status_code == 200

        events = client.get("/memory/ingestions", headers=headers)
        assert events.status_code == 200
        assert any(
            item["status"] == "deleted" and item["notes_json"].get("operation") == "delete_long_term"
            for item in events.json()
        )
        assert any(
            item["status"] == "deleted" and item["notes_json"].get("operation") == "delete_short_term"
            for item in events.json()
        )


def test_memory_ingestion_and_failure_ops():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Ops KB", "description": "ops"}).json()
        kb_id = kb["id"]

        ingestion = client.post(
            "/memory/ingest",
            headers=headers,
            json={
                "session_id": "chat-s1",
                "kb_id": kb_id,
                "query": "用户希望以后回答更简洁",
                "answer": "好的，后续我会保持简洁回答。",
                "source": "bot",
                "trace_id": "trace-test",
                "source_refs": ["/personal/uploads/profile.txt"],
            },
        )
        assert ingestion.status_code == 200
        payload = ingestion.json()
        assert payload["event"]["short_term_created"] >= 1
        assert payload["event"]["long_term_created"] >= 1

        listed_events = client.get("/memory/ingestions", headers=headers)
        assert listed_events.status_code == 200
        assert any(item["trace_id"] == "trace-test" for item in listed_events.json())

        short_memories = client.get("/memory/short-term?session_id=chat-s1", headers=headers)
        assert short_memories.status_code == 200
        assert any(item["memory_type"] == "recent_turn" for item in short_memories.json())

        long_memories = client.get("/memory/long-term", headers=headers)
        assert long_memories.status_code == 200
        assert any(item["category"] == "preference" for item in long_memories.json())

        duplicate = client.post(
            "/memory/ingest",
            headers=headers,
            json={
                "session_id": "chat-s1",
                "kb_id": kb_id,
                "query": "用户希望以后回答更简洁",
                "answer": "好的，后续我会保持简洁回答。",
                "source": "bot",
                "trace_id": "trace-test-2",
                "source_refs": ["/personal/uploads/profile.txt"],
            },
        )
        assert duplicate.status_code == 200
        duplicate_payload = duplicate.json()
        assert duplicate_payload["event"]["short_term_created"] == 0
        assert duplicate_payload["event"]["long_term_created"] == 0

        failed_task = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": ["/../not-allowed.txt"]},
        )
        assert failed_task.status_code == 200

        processed = client.post("/tasks/process-pending", headers=headers)
        assert processed.status_code == 200

        failures = client.get("/ops/tasks/failures")
        assert failures.status_code == 200
        assert any(item["id"] == failed_task.json()["id"] for item in failures.json())


def test_sqlite_engine_uses_busy_timeout_and_wal():
    with engine.connect() as conn:
        if conn.dialect.name != "sqlite":
            return
        journal_mode = str(conn.exec_driver_sql("PRAGMA journal_mode").scalar() or "").lower()
        busy_timeout = int(conn.exec_driver_sql("PRAGMA busy_timeout").scalar() or 0)
        foreign_keys = int(conn.exec_driver_sql("PRAGMA foreign_keys").scalar() or 0)
        assert journal_mode in {"wal", "memory"}
        assert busy_timeout >= 15000
        assert foreign_keys == 1


def test_worker_serializes_processing_and_keeps_tasks_queued_when_busy():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Queue KB", "description": "queue"}).json()
        kb_id = kb["id"]

        created = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": ["/../queued-not-allowed.txt"]},
        )
        assert created.status_code == 200
        task_id = created.json()["id"]
        assert created.json()["queue_state"] == "queued"
        assert created.json()["queue_position"] == 1

        worker = Worker()
        worker.lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        with worker.lock_file_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            blocked = client.post("/tasks/process-pending", headers=headers)
            assert blocked.status_code == 200
            blocked_payload = blocked.json()
            assert blocked_payload["processed"] == 0
            assert blocked_payload["pending"] >= 1
            assert blocked_payload["worker_busy"] is True

            pending_task = client.get(f"/tasks/{task_id}", headers=headers)
            assert pending_task.status_code == 200
            assert pending_task.json()["status"] == "pending"
            assert pending_task.json()["queue_state"] == "queued"

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        resumed = client.post("/tasks/process-pending", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["processed"] >= 1

        finished_task = client.get(f"/tasks/{task_id}", headers=headers)
        assert finished_task.status_code == 200
        assert finished_task.json()["status"] in {"failed", "partial_success", "succeeded"}


def test_cancel_pending_task_marks_canceled_without_processing():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Cancel KB", "description": "cancel"}).json()
        created = client.post(
            f"/kbs/{kb['id']}/tasks/import",
            headers=headers,
            json={"source_paths": ["/../cancel-not-allowed.txt"]},
        )
        assert created.status_code == 200
        task_id = created.json()["id"]

        canceled = client.post(f"/tasks/{task_id}/cancel", headers=headers)
        assert canceled.status_code == 200
        payload = canceled.json()
        assert payload["status"] == "canceled"
        assert payload["cancelable"] is False
        assert payload["stats_json"]["rollback"]["applied"] is False

        processed = client.post("/tasks/process-pending", headers=headers)
        assert processed.status_code == 200
        assert processed.json()["processed"] == 0


def test_delete_kb_cleans_related_resources():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Delete KB", "description": "cleanup"}).json()
        kb_id = kb["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/uploads"},
            files={"file": ("delete-kb.txt", b"delete kb cleanup content", "text/plain")},
        )
        assert upload.status_code == 200
        source_path = upload.json()["warehouse_path"]

        bind = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": source_path, "scope_type": "file"},
        )
        assert bind.status_code == 200

        import_task = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": [source_path]},
        )
        assert import_task.status_code == 200
        processed = client.post("/tasks/process-pending", headers=headers)
        assert processed.status_code == 200
        assert processed.json()["processed"] >= 1

        pending_reindex = client.post(
            f"/kbs/{kb_id}/tasks/reindex",
            headers=headers,
            json={"source_paths": [source_path]},
        )
        assert pending_reindex.status_code == 200

        memory = client.post(
            "/memory/long-term",
            headers=headers,
            json={"kb_id": kb_id, "content": "kb scoped memory", "category": "note", "source": "console", "score": 100},
        )
        assert memory.status_code == 200

        ingestion = client.post(
            "/memory/ingest",
            headers=headers,
            json={
                "session_id": "delete-kb-session",
                "kb_id": kb_id,
                "query": "cleanup?",
                "answer": "cleanup done",
                "source": "bot",
            },
        )
        assert ingestion.status_code == 200

        deleted = client.delete(f"/kbs/{kb_id}", headers=headers)
        assert deleted.status_code == 200

        kbs = client.get("/kbs", headers=headers)
        assert kbs.status_code == 200
        assert all(item["id"] != kb_id for item in kbs.json())

        tasks = client.get("/tasks", headers=headers)
        assert tasks.status_code == 200
        assert all(item["kb_id"] != kb_id for item in tasks.json())

        memories = client.get("/memory/long-term", headers=headers)
        assert memories.status_code == 200
        assert all(item["kb_id"] != kb_id for item in memories.json())

        events = client.get("/memory/ingestions", headers=headers)
        assert events.status_code == 200
        assert all(item["kb_id"] != kb_id for item in events.json())

        get_deleted_kb = client.get(f"/kbs/{kb_id}", headers=headers)
        assert get_deleted_kb.status_code == 404


def test_kb_config_update_reindexes_existing_documents_and_uses_latest_limits():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post(
            "/kbs",
            headers=headers,
            json={
                "name": "Config KB",
                "description": "config",
                "retrieval_config": {
                    "chunk_size": 180,
                    "chunk_overlap": 0,
                    "retrieval_top_k": 4,
                    "memory_top_k": 3,
                    "embedding_model": "text-embedding-3-small",
                },
            },
        )
        assert kb.status_code == 200
        kb_id = kb.json()["id"]

        content = ("knowledge search chunk metadata verification " * 40).encode()
        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/uploads"},
            files={"file": ("config.txt", content, "text/plain")},
        )
        assert upload.status_code == 200
        source_path = upload.json()["warehouse_path"]

        bind = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": source_path, "scope_type": "file"},
        )
        assert bind.status_code == 200

        task = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": [source_path]},
        )
        assert task.status_code == 200

        processed = client.post("/tasks/process-pending", headers=headers)
        assert processed.status_code == 200
        assert processed.json()["processed"] >= 1

        documents = client.get(f"/kbs/{kb_id}/documents", headers=headers)
        assert documents.status_code == 200
        doc_id = documents.json()[0]["id"]

        before_detail = client.get(f"/kbs/{kb_id}/documents/{doc_id}", headers=headers)
        assert before_detail.status_code == 200
        before_payload = before_detail.json()
        assert before_payload["chunk_count"] >= 2

        before_search = client.post(f"/kbs/{kb_id}/search", headers=headers, json={"query": "knowledge search"})
        assert before_search.status_code == 200
        assert len(before_search.json()) >= 2

        for index in range(3):
            created_short = client.post(
                "/memory/short-term",
                headers=headers,
                json={"session_id": "kb-config-session", "memory_type": "summary", "content": f"short memory {index}"},
            )
            assert created_short.status_code == 200
            created_long = client.post(
                "/memory/long-term",
                headers=headers,
                json={"content": f"long memory {index}", "category": "note", "source": "console", "score": 100},
            )
            assert created_long.status_code == 200

        updated = client.patch(
            f"/kbs/{kb_id}",
            headers=headers,
            json={
                "retrieval_config": {
                    "chunk_size": 60,
                    "chunk_overlap": 0,
                    "retrieval_top_k": 1,
                    "memory_top_k": 1,
                }
            },
        )
        assert updated.status_code == 200
        assert updated.json()["retrieval_config"]["embedding_model"] == "text-embedding-3-small"

        tasks = client.get("/tasks", headers=headers)
        assert tasks.status_code == 200
        assert any(
            item["kb_id"] == kb_id and item["task_type"] == "reindex" and item["status"] == "pending"
            for item in tasks.json()
        )

        processed_reindex = client.post("/tasks/process-pending", headers=headers)
        assert processed_reindex.status_code == 200
        assert processed_reindex.json()["processed"] >= 1

        after_detail = client.get(f"/kbs/{kb_id}/documents/{doc_id}", headers=headers)
        assert after_detail.status_code == 200
        after_payload = after_detail.json()
        assert after_payload["chunk_count"] > before_payload["chunk_count"]
        assert after_payload["chunks"][0]["metadata"]["chunk_strategy"]
        assert after_payload["chunks"][0]["embedding_model"] == "text-embedding-3-small"

        after_search = client.post(f"/kbs/{kb_id}/search", headers=headers, json={"query": "knowledge search"})
        assert after_search.status_code == 200
        assert len(after_search.json()) == 1

        context = client.post(
            "/retrieval-context",
            headers=headers,
            json={"session_id": "kb-config-session", "query": "knowledge search", "kb_ids": [kb_id]},
        )
        assert context.status_code == 200
        payload = context.json()
        assert len(payload["short_term_memory_blocks"]) == 1
        assert len(payload["long_term_memory_blocks"]) == 1
