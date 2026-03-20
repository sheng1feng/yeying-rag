from __future__ import annotations

from datetime import timedelta
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from knowledge.db.session import engine, session_scope
from knowledge.main import app
from knowledge.models import ImportTask
from knowledge.services.warehouse_scope import warehouse_app_path, warehouse_app_root, warehouse_default_upload_dir
from knowledge.utils.time import utc_now
from knowledge.workers.runner import Worker


APP_ROOT = warehouse_app_root()
UPLOADS_ROOT = warehouse_default_upload_dir()


def _app_path(relative_path: str) -> str:
    return warehouse_app_path(relative_path)


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
            data={"target_dir": UPLOADS_ROOT},
            files={"file": ("hello.txt", b"hello warehouse and knowledge search", "text/plain")},
        )
        assert upload.status_code == 200
        upload_data = upload.json()
        assert upload_data["warehouse_path"].startswith(f"{APP_ROOT}/")

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

        ops_overview = client.get("/ops/overview", headers=headers)
        assert ops_overview.status_code == 200
        assert ops_overview.json()["knowledge_bases"] >= 1

        ops_workers = client.get("/ops/workers", headers=headers)
        assert ops_workers.status_code == 200
        assert len(ops_workers.json()) >= 1


def test_warehouse_app_only_paths_reject_personal_scope():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "App Only KB", "description": "app only"}).json()
        kb_id = kb["id"]

        browse = client.get("/warehouse/browse?path=/personal", headers=headers)
        assert browse.status_code == 400
        assert APP_ROOT in browse.json()["detail"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/uploads"},
            files={"file": ("blocked.txt", b"should fail", "text/plain")},
        )
        assert upload.status_code == 400
        assert APP_ROOT in upload.json()["detail"]

        bind = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": "/personal/uploads/blocked.txt", "scope_type": "file"},
        )
        assert bind.status_code == 400
        assert APP_ROOT in bind.json()["detail"]

        task = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": ["/personal/uploads/blocked.txt"]},
        )
        assert task.status_code == 400
        assert APP_ROOT in task.json()["detail"]


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
                "source_refs": [_app_path("uploads/profile.txt")],
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
                "source_refs": [_app_path("uploads/profile.txt")],
            },
        )
        assert duplicate.status_code == 200
        duplicate_payload = duplicate.json()
        assert duplicate_payload["event"]["short_term_created"] == 0
        assert duplicate_payload["event"]["long_term_created"] == 0

        failed_task = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": [_app_path("missing/not-allowed.txt")]},
        )
        assert failed_task.status_code == 200
        with session_scope() as db:
            task = db.get(ImportTask, failed_task.json()["id"])
            assert task is not None
            task.source_paths = ["/../not-allowed.txt"]

        processed = client.post("/tasks/process-pending", headers=headers)
        assert processed.status_code == 200

        failures = client.get("/ops/tasks/failures", headers=headers)
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


def test_worker_processes_pending_tasks_without_global_run_lease():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Queue KB", "description": "queue"}).json()
        kb_id = kb["id"]

        created = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": [_app_path("missing/queued-not-allowed.txt")]},
        )
        assert created.status_code == 200
        task_id = created.json()["id"]
        assert created.json()["queue_state"] == "queued"
        assert created.json()["queue_position"] == 1

        resumed = client.post("/tasks/process-pending", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["processed"] >= 1
        assert resumed.json()["worker_busy"] is False
        finished_task = client.get(f"/tasks/{task_id}", headers=headers)
        assert finished_task.status_code == 200
        assert finished_task.json()["status"] in {"failed", "partial_success", "succeeded"}
        assert finished_task.json()["claimed_by"] is None


def test_worker_reclaims_stale_running_task_and_reprocesses_it():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Lease KB", "description": "lease"}).json()
        created = client.post(
            f"/kbs/{kb['id']}/tasks/import",
            headers=headers,
            json={"source_paths": [_app_path("missing/stale-lease-not-allowed.txt")]},
        )
        assert created.status_code == 200

        worker = Worker()
        with session_scope() as db:
            task = db.get(ImportTask, created.json()["id"])
            assert task is not None
            task.status = "running"
            task.claimed_by = "stale-worker"
            task.claimed_at = utc_now() - timedelta(seconds=worker.settings.worker_run_lease_ttl_seconds + 5)
            task.started_at = utc_now() - timedelta(seconds=worker.settings.worker_run_lease_ttl_seconds + 5)
            task.heartbeat_at = utc_now() - timedelta(seconds=worker.settings.worker_run_lease_ttl_seconds + 5)
            task.last_stage = f"processing:{_app_path('missing/stale-lease-not-allowed.txt')}"

        processed = client.post("/tasks/process-pending", headers=headers)
        assert processed.status_code == 200
        assert processed.json()["processed"] >= 1
        assert processed.json()["worker_busy"] is False
        task_after = client.get(f"/tasks/{created.json()['id']}", headers=headers)
        assert task_after.status_code == 200
        assert task_after.json()["status"] in {"failed", "partial_success", "succeeded"}
        assert task_after.json()["claimed_by"] is None


def test_cancel_pending_task_marks_canceled_without_processing():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Cancel KB", "description": "cancel"}).json()
        created = client.post(
            f"/kbs/{kb['id']}/tasks/import",
            headers=headers,
            json={"source_paths": [_app_path("missing/cancel-not-allowed.txt")]},
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


def test_cancel_running_task_marks_cancel_requested_in_db():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Cancel Running KB", "description": "cancel-running"}).json()
        created = client.post(
            f"/kbs/{kb['id']}/tasks/import",
            headers=headers,
            json={"source_paths": [_app_path("missing/cancel-running-not-allowed.txt")]},
        )
        assert created.status_code == 200
        task_id = created.json()["id"]

        with session_scope() as db:
            task = db.get(ImportTask, task_id)
            assert task is not None
            task.status = "running"

        canceled = client.post(f"/tasks/{task_id}/cancel", headers=headers)
        assert canceled.status_code == 200
        payload = canceled.json()
        assert payload["status"] == "cancel_requested"
        assert payload["queue_state"] == "cancelling"
        assert payload["cancelable"] is True


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
            data={"target_dir": UPLOADS_ROOT},
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
            data={"target_dir": UPLOADS_ROOT},
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


def test_ops_endpoints_require_auth():
    with TestClient(app) as client:
        overview = client.get("/ops/overview")
        assert overview.status_code == 401

        account = Account.create()
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        authed_overview = client.get("/ops/overview", headers=headers)
        assert authed_overview.status_code == 200
        assert "knowledge_bases" in authed_overview.json()


def test_delete_document_route_cleans_vector_store(monkeypatch):
    from knowledge.api import routes_documents

    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Cleanup KB", "description": "cleanup"}).json()
        kb_id = kb["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("cleanup")},
            files={"file": ("cleanup.txt", b"cleanup vector store document", "text/plain")},
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
        process = client.post("/tasks/process-pending", headers=headers)
        assert process.status_code == 200

        documents = client.get(f"/kbs/{kb_id}/documents", headers=headers)
        assert documents.status_code == 200
        document_id = documents.json()[0]["id"]

        deleted_vector_ids: list[list[str]] = []
        original_delete_vectors = routes_documents.document_index_service.vector_store.delete_vectors

        def spy_delete_vectors(vector_ids: list[str]) -> None:
            deleted_vector_ids.append(list(vector_ids))
            original_delete_vectors(vector_ids)

        monkeypatch.setattr(routes_documents.document_index_service.vector_store, "delete_vectors", spy_delete_vectors)

        delete_response = client.delete(f"/kbs/{kb_id}/documents/{document_id}", headers=headers)
        assert delete_response.status_code == 200
        assert deleted_vector_ids
        assert deleted_vector_ids[0]

        documents_after_delete = client.get(f"/kbs/{kb_id}/documents", headers=headers)
        assert documents_after_delete.status_code == 200
        assert documents_after_delete.json() == []


def test_directory_scope_delete_task_removes_nested_documents_and_updates_binding():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Directory KB", "description": "directory scope"}).json()
        kb_id = kb["id"]

        first_upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("dir-scope")},
            files={"file": ("a.txt", b"first nested document", "text/plain")},
        )
        assert first_upload.status_code == 200

        second_upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("dir-scope/nested")},
            files={"file": ("b.txt", b"second nested document", "text/plain")},
        )
        assert second_upload.status_code == 200

        binding = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": _app_path("dir-scope"), "scope_type": "directory"},
        )
        assert binding.status_code == 200
        assert binding.json()["last_imported_at"] is None

        import_task = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": [_app_path("dir-scope")]},
        )
        assert import_task.status_code == 200
        process_import = client.post("/tasks/process-pending", headers=headers)
        assert process_import.status_code == 200

        documents_after_import = client.get(f"/kbs/{kb_id}/documents", headers=headers)
        assert documents_after_import.status_code == 200
        assert len(documents_after_import.json()) == 2

        bindings_after_import = client.get(f"/kbs/{kb_id}/bindings", headers=headers)
        assert bindings_after_import.status_code == 200
        assert bindings_after_import.json()[0]["last_imported_at"] is not None

        delete_task = client.post(
            f"/kbs/{kb_id}/tasks/delete",
            headers=headers,
            json={"source_paths": [_app_path("dir-scope")]},
        )
        assert delete_task.status_code == 200
        process_delete = client.post("/tasks/process-pending", headers=headers)
        assert process_delete.status_code == 200

        documents_after_delete = client.get(f"/kbs/{kb_id}/documents", headers=headers)
        assert documents_after_delete.status_code == 200
        assert documents_after_delete.json() == []


def test_binding_based_task_endpoints_create_tasks_from_enabled_bindings():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Binding Tasks KB", "description": "binding tasks"}).json()
        kb_id = kb["id"]

        upload_a = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("binding-tasks")},
            files={"file": ("alpha.txt", b"alpha binding task content", "text/plain")},
        )
        assert upload_a.status_code == 200

        upload_b = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("binding-tasks")},
            files={"file": ("beta.txt", b"beta binding task content", "text/plain")},
        )
        assert upload_b.status_code == 200

        binding_a = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": upload_a.json()["warehouse_path"], "scope_type": "file"},
        )
        assert binding_a.status_code == 200

        binding_b = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": upload_b.json()["warehouse_path"], "scope_type": "file"},
        )
        assert binding_b.status_code == 200

        import_task = client.post(f"/kbs/{kb_id}/tasks/import-from-bindings", headers=headers, json={})
        assert import_task.status_code == 200
        import_payload = import_task.json()
        assert import_payload["task_type"] == "import"
        assert import_payload["stats_json"]["created_from"] == "bindings"
        assert set(import_payload["stats_json"]["binding_ids"]) == {binding_a.json()["id"], binding_b.json()["id"]}
        assert len(import_payload["source_paths"]) == 2

        processed_import = client.post("/tasks/process-pending", headers=headers)
        assert processed_import.status_code == 200

        documents_after_import = client.get(f"/kbs/{kb_id}/documents", headers=headers)
        assert documents_after_import.status_code == 200
        assert len(documents_after_import.json()) == 2

        reindex_task = client.post(
            f"/kbs/{kb_id}/tasks/reindex-from-bindings",
            headers=headers,
            json={"binding_ids": [binding_a.json()["id"]]},
        )
        assert reindex_task.status_code == 200
        reindex_payload = reindex_task.json()
        assert reindex_payload["task_type"] == "reindex"
        assert reindex_payload["stats_json"]["binding_ids"] == [binding_a.json()["id"]]
        assert reindex_payload["source_paths"] == [upload_a.json()["warehouse_path"]]

        delete_task = client.post(f"/kbs/{kb_id}/tasks/delete-from-bindings", headers=headers, json={})
        assert delete_task.status_code == 409

        processed_reindex = client.post("/tasks/process-pending", headers=headers)
        assert processed_reindex.status_code == 200

        delete_task = client.post(f"/kbs/{kb_id}/tasks/delete-from-bindings", headers=headers, json={})
        assert delete_task.status_code == 200
        assert delete_task.json()["task_type"] == "delete"

        processed_delete = client.post("/tasks/process-pending", headers=headers)
        assert processed_delete.status_code == 200

        documents_after_delete = client.get(f"/kbs/{kb_id}/documents", headers=headers)
        assert documents_after_delete.status_code == 200
        assert documents_after_delete.json() == []


def test_duplicate_active_task_reuses_existing_task():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Duplicate Task KB", "description": "duplicate"}).json()
        kb_id = kb["id"]

        created = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": [_app_path("dup"), _app_path("dup/file.txt")]},
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert created_payload["source_paths"] == [_app_path("dup")]

        duplicate = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": [_app_path("dup")]},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == created_payload["id"]


def test_binding_based_task_endpoints_validate_requested_binding_ids():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Binding Validation KB", "description": "binding validation"}).json()
        kb_id = kb["id"]

        no_binding_task = client.post(f"/kbs/{kb_id}/tasks/import-from-bindings", headers=headers, json={})
        assert no_binding_task.status_code == 400
        assert "no enabled bindings" in no_binding_task.json()["detail"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("binding-validation")},
            files={"file": ("gamma.txt", b"gamma binding validation", "text/plain")},
        )
        assert upload.status_code == 200

        binding = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": upload.json()["warehouse_path"], "scope_type": "file"},
        )
        assert binding.status_code == 200

        invalid_binding_task = client.post(
            f"/kbs/{kb_id}/tasks/import-from-bindings",
            headers=headers,
            json={"binding_ids": [binding.json()["id"], binding.json()["id"] + 999]},
        )
        assert invalid_binding_task.status_code == 400
        assert "binding not found" in invalid_binding_task.json()["detail"]


def test_binding_status_management_and_kb_workbench_summary():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Workbench KB", "description": "workbench"}).json()
        kb_id = kb["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("workbench")},
            files={"file": ("workbench.txt", b"binding workbench content", "text/plain")},
        )
        assert upload.status_code == 200

        binding = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": upload.json()["warehouse_path"], "scope_type": "file"},
        )
        assert binding.status_code == 200
        binding_id = binding.json()["id"]

        initial_bindings = client.get(f"/kbs/{kb_id}/bindings", headers=headers)
        assert initial_bindings.status_code == 200
        assert initial_bindings.json()[0]["sync_status"] == "pending_sync"
        assert initial_bindings.json()[0]["document_count"] == 0

        queued_task = client.post(
            f"/kbs/{kb_id}/tasks/import-from-bindings",
            headers=headers,
            json={"binding_ids": [binding_id]},
        )
        assert queued_task.status_code == 200

        syncing_bindings = client.get(f"/kbs/{kb_id}/bindings", headers=headers)
        assert syncing_bindings.status_code == 200
        assert syncing_bindings.json()[0]["sync_status"] == "syncing"
        assert syncing_bindings.json()[0]["active_task_count"] >= 1

        processed = client.post("/tasks/process-pending", headers=headers)
        assert processed.status_code == 200

        indexed_bindings = client.get(f"/kbs/{kb_id}/bindings", headers=headers)
        assert indexed_bindings.status_code == 200
        indexed_binding = indexed_bindings.json()[0]
        assert indexed_binding["sync_status"] == "indexed"
        assert indexed_binding["document_count"] == 1
        assert indexed_binding["chunk_count"] >= 1
        assert indexed_binding["latest_task_status"] == "succeeded"

        disabled = client.patch(
            f"/kbs/{kb_id}/bindings/{binding_id}",
            headers=headers,
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        assert disabled.json()["sync_status"] == "disabled"

        workbench = client.get(f"/kbs/{kb_id}/workbench", headers=headers)
        assert workbench.status_code == 200
        workbench_payload = workbench.json()
        assert workbench_payload["binding_status_counts"]["total"] == 1
        assert workbench_payload["binding_status_counts"]["disabled"] == 1
        assert workbench_payload["stats"]["documents_count"] == 1
        assert workbench_payload["recent_tasks"]

        no_enabled_binding_task = client.post(f"/kbs/{kb_id}/tasks/import-from-bindings", headers=headers, json={})
        assert no_enabled_binding_task.status_code == 400
        assert "no enabled bindings" in no_enabled_binding_task.json()["detail"]
