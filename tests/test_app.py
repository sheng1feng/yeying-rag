from __future__ import annotations

from datetime import timedelta
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from knowledge.db.session import engine, session_scope
from knowledge.main import app
from knowledge.models import ImportTask
from knowledge.utils.time import utc_now
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

        ops_overview = client.get("/ops/overview", headers=headers)
        assert ops_overview.status_code == 200
        assert ops_overview.json()["knowledge_bases"] >= 1

        ops_workers = client.get("/ops/workers", headers=headers)
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
            json={"source_paths": ["/../queued-not-allowed.txt"]},
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
            json={"source_paths": ["/../stale-lease-not-allowed.txt"]},
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
            task.last_stage = "processing:/../stale-lease-not-allowed.txt"

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


def test_cancel_running_task_marks_cancel_requested_in_db():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Cancel Running KB", "description": "cancel-running"}).json()
        created = client.post(
            f"/kbs/{kb['id']}/tasks/import",
            headers=headers,
            json={"source_paths": ["/../cancel-running-not-allowed.txt"]},
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


def test_layered_retrieval_apis_and_bot_context_v2():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Bot KB", "description": "bot"}).json()
        kb_id = kb["id"]

        content = b"wallet knowledge retrieval evidence for bot context assembly"
        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/uploads"},
            files={"file": ("bot-context.txt", content, "text/plain")},
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

        short_memory = client.post(
            "/memory/short-term",
            headers=headers,
            json={
                "session_id": "bot-session-v2",
                "memory_type": "summary",
                "content": "请保持回答简洁并优先引用知识库证据",
            },
        )
        assert short_memory.status_code == 200

        long_memory = client.post(
            "/memory/long-term",
            headers=headers,
            json={
                "kb_id": kb_id,
                "content": "用户偏好：优先返回带来源的知识证据",
                "category": "preference",
                "source": "bot",
                "score": 95,
            },
        )
        assert long_memory.status_code == 200

        search = client.post(
            "/retrieval/search",
            headers=headers,
            json={
                "query": "knowledge evidence",
                "kb_ids": [kb_id],
                "source_scope": [source_path],
                "debug": True,
            },
        )
        assert search.status_code == 200
        search_payload = search.json()
        assert search_payload["hits"]
        assert search_payload["hits"][0]["source_path"] == source_path
        assert search_payload["debug"]["search_filters"]["source_paths"] == [source_path]

        retrieve = client.post(
            "/retrieval/retrieve",
            headers=headers,
            json={
                "query": "knowledge evidence",
                "kb_ids": [kb_id],
                "source_scope": [source_path],
                "retrieval_policy": {"top_k": 2, "token_budget": 80},
                "debug": True,
            },
        )
        assert retrieve.status_code == 200
        retrieve_payload = retrieve.json()
        assert retrieve_payload["knowledge_hits"]
        assert retrieve_payload["source_refs"] == [source_path]
        assert retrieve_payload["applied_policy"]["top_k"] == 2
        assert retrieve_payload["applied_policy"]["token_budget"] == 80

        recall = client.post(
            "/retrieval/recall-memory",
            headers=headers,
            json={
                "query": "请保持简洁并给出知识证据",
                "session_id": "bot-session-v2",
                "kb_ids": [kb_id],
                "retrieval_policy": {"memory_top_k": 2},
                "debug": True,
            },
        )
        assert recall.status_code == 200
        recall_payload = recall.json()
        assert recall_payload["short_term_hits"]
        assert recall_payload["long_term_hits"]
        assert recall_payload["debug"]["memory_strategy"]

        bot_context = client.post(
            "/bot/retrieval-context",
            headers=headers,
            json={
                "query": "please answer with knowledge evidence",
                "kb_ids": [kb_id],
                "user_identity": "user-42",
                "session_id": "bot-session-v2",
                "conversation_id": "conv-007",
                "bot_id": "wallet-bot",
                "app_id": "wallet-app",
                "intent": "qa",
                "scene": "customer_support",
                "source_scope": [source_path],
                "token_budget": 90,
                "debug": True,
            },
        )
        assert bot_context.status_code == 200
        context_payload = bot_context.json()
        assert context_payload["request_context"]["bot_id"] == "wallet-bot"
        assert context_payload["request_context"]["app_id"] == "wallet-app"
        assert context_payload["source_refs"] == [source_path]
        assert context_payload["knowledge_hits"]
        assert context_payload["short_term_memory_hits"]
        assert context_payload["long_term_memory_hits"]
        assert context_payload["context_sections"]
        assert "Knowledge Evidence" in context_payload["assembled_context"]
        assert context_payload["debug"]["request_context"]["conversation_id"] == "conv-007"

        assembled = client.post(
            "/retrieval/assemble-context",
            headers=headers,
            json={
                "query": "please answer with knowledge evidence",
                "request_context": {"bot_id": "wallet-bot"},
                "knowledge_hits": context_payload["knowledge_hits"],
                "short_term_hits": context_payload["short_term_memory_hits"],
                "long_term_hits": context_payload["long_term_memory_hits"],
                "retrieval_policy": {"max_context_chars": 260},
                "debug": True,
            },
        )
        assert assembled.status_code == 200
        assembled_payload = assembled.json()
        assert assembled_payload["context_sections"]
        assert assembled_payload["applied_policy"]["max_context_chars"] == 400
        assert len(assembled_payload["assembled_context"]) <= 450


def test_recall_memory_scopes_long_term_by_kb_and_session():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb_a = client.post("/kbs", headers=headers, json={"name": "KB-A", "description": "a"}).json()
        kb_b = client.post("/kbs", headers=headers, json={"name": "KB-B", "description": "b"}).json()

        created_short = client.post(
            "/memory/short-term",
            headers=headers,
            json={"session_id": "session-a", "memory_type": "summary", "content": "project alpha summary"},
        )
        assert created_short.status_code == 200

        other_short = client.post(
            "/memory/short-term",
            headers=headers,
            json={"session_id": "session-b", "memory_type": "summary", "content": "project alpha from other session"},
        )
        assert other_short.status_code == 200

        kb_a_memory = client.post(
            "/memory/long-term",
            headers=headers,
            json={
                "kb_id": kb_a["id"],
                "content": "用户事实：project alpha belongs to kb a",
                "category": "fact",
                "source": "bot",
                "score": 90,
            },
        )
        assert kb_a_memory.status_code == 200

        kb_b_memory = client.post(
            "/memory/long-term",
            headers=headers,
            json={
                "kb_id": kb_b["id"],
                "content": "用户事实：project alpha belongs to kb b",
                "category": "fact",
                "source": "bot",
                "score": 90,
            },
        )
        assert kb_b_memory.status_code == 200

        global_memory = client.post(
            "/memory/long-term",
            headers=headers,
            json={
                "content": "用户偏好：project alpha responses should include source refs",
                "category": "preference",
                "source": "bot",
                "score": 88,
            },
        )
        assert global_memory.status_code == 200

        recall = client.post(
            "/retrieval/recall-memory",
            headers=headers,
            json={
                "query": "project alpha",
                "session_id": "session-a",
                "kb_ids": [kb_a["id"]],
                "retrieval_policy": {"memory_top_k": 5},
                "debug": True,
            },
        )
        assert recall.status_code == 200
        payload = recall.json()
        assert payload["short_term_hits"]
        assert all(item["session_id"] == "session-a" for item in payload["short_term_hits"])
        assert payload["long_term_hits"]
        assert all(item["kb_id"] in (None, kb_a["id"]) for item in payload["long_term_hits"])
        assert not any(item["kb_id"] == kb_b["id"] for item in payload["long_term_hits"])


def test_neutral_retrieval_context_endpoint_uses_conversation_scope_and_caller():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Neutral API KB", "description": "neutral"}).json()
        kb_id = kb["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/uploads"},
            files={"file": ("neutral-api.txt", b"neutral retrieval context knowledge evidence", "text/plain")},
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

        ingest = client.post(
            "/memory/ingest",
            headers=headers,
            json={
                "session_id": "neutral-session",
                "memory_namespace": "robot-alpha",
                "kb_id": kb_id,
                "query": "请保持输出简洁",
                "answer": "好的，我会保持简洁并引用知识证据。",
                "source": "bot",
                "source_refs": [source_path],
            },
        )
        assert ingest.status_code == 200
        assert ingest.json()["memory_namespace"] == "robot-alpha"

        context = client.post(
            "/retrieval/context",
            headers=headers,
            json={
                "query": "knowledge evidence",
                "conversation": {
                    "session_id": "neutral-session",
                    "conversation_id": "conv-neutral-1",
                    "memory_namespace": "robot-alpha",
                    "scene": "support",
                    "intent": "qa",
                },
                "scope": {
                    "kb_ids": [kb_id],
                    "source_scope": [source_path],
                    "filters": {"source_kinds": ["personal"]},
                },
                "policy": {"top_k": 2, "memory_top_k": 2, "token_budget": 120},
                "caller": {"app_name": "bot-platform", "request_id": "req-neutral-1"},
                "debug": True,
            },
        )
        assert context.status_code == 200
        payload = context.json()
        assert payload["conversation"]["memory_namespace"] == "robot-alpha"
        assert payload["scope"]["kb_ids"] == [kb_id]
        assert payload["caller"]["app_name"] == "bot-platform"
        assert payload["knowledge"]["hits"]
        assert payload["knowledge"]["source_refs"] == [source_path]
        assert payload["memory"]["short_term_hits"]
        assert payload["memory"]["short_term_hits"][0]["metadata"]["memory_namespace"] == "robot-alpha"
        assert payload["context"]["sections"]
        assert payload["trace"]["applied_policy"]["token_budget"] == 120
        assert payload["debug"]["request_context"]["conversation"]["conversation_id"] == "conv-neutral-1"


def test_neutral_retrieval_context_omits_debug_when_disabled():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "No Debug KB", "description": "contract"}).json()

        context = client.post(
            "/retrieval/context",
            headers=headers,
            json={
                "query": "contract only",
                "conversation": {"session_id": "contract-session"},
                "scope": {"kb_ids": [kb["id"]]},
                "policy": {"top_k": 2},
            },
        )
        assert context.status_code == 200
        payload = context.json()
        assert set(payload.keys()) == {"query", "conversation", "scope", "caller", "knowledge", "memory", "context", "trace"}
        assert "debug" not in payload
        assert "policy" not in payload
        assert payload["conversation"] == {"session_id": "contract-session"}
        assert payload["caller"] == {}


def test_legacy_retrieval_context_adapter_reuses_generate_context(monkeypatch):
    from knowledge.api import routes_search

    account = Account.create()
    captured: dict = {}

    def fake_generate_context(db, wallet_address, kb_ids, query, **kwargs):
        captured["wallet_address"] = wallet_address
        captured["kb_ids"] = kb_ids
        captured["query"] = query
        captured["kwargs"] = kwargs
        now = utc_now()
        return {
            "query": query,
            "kb_ids": kb_ids,
            "request_context": kwargs.get("request_context") or {},
            "knowledge_hits": [
                {
                    "chunk_id": 11,
                    "kb_id": kb_ids[0],
                    "document_id": 22,
                    "source_path": "/personal/uploads/legacy.txt",
                    "text": "legacy adapter knowledge",
                    "score": 0.91,
                    "metadata": {},
                }
            ],
            "short_term_memory_hits": [
                {
                    "id": 31,
                    "memory_kind": "short_term",
                    "memory_type": "summary",
                    "content": "legacy short term memory",
                    "score": 0.88,
                    "created_at": now,
                }
            ],
            "long_term_memory_hits": [
                {
                    "id": 41,
                    "memory_kind": "long_term",
                    "memory_type": "preference",
                    "content": "legacy long term memory",
                    "score": 0.77,
                    "created_at": now,
                }
            ],
            "context_sections": [],
            "assembled_context": "",
            "source_refs": ["/personal/uploads/legacy.txt"],
            "applied_policy": {
                "top_k": 3,
                "memory_top_k": 4,
                "token_budget": None,
                "max_context_chars": 4800,
                "include_knowledge": True,
                "include_short_term": True,
                "include_long_term": True,
            },
            "trace_id": "trace-legacy-adapter",
        }

    monkeypatch.setattr(routes_search.service, "generate_context", fake_generate_context)

    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/retrieval-context",
            headers=headers,
            json={"session_id": "legacy-session", "query": "legacy query", "kb_ids": [7], "top_k": 3},
        )

    assert response.status_code == 200
    payload = response.json()
    assert captured["kb_ids"] == [7]
    assert captured["query"] == "legacy query"
    assert captured["kwargs"]["session_id"] == "legacy-session"
    assert captured["kwargs"]["top_k"] == 3
    assert captured["kwargs"]["request_context"]["compatibility_mode"] == "legacy_retrieval_context"
    assert payload["kb_blocks"][0]["source_path"] == "/personal/uploads/legacy.txt"
    assert payload["short_term_memory_blocks"][0]["content"] == "legacy short term memory"
    assert payload["scores"]["applied_policy"]["top_k"] == 3
    assert payload["trace_id"] == "trace-legacy-adapter"


def test_legacy_and_neutral_context_share_policy_resolution():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Compat KB", "description": "compat"}).json()
        kb_id = kb["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/uploads"},
            files={"file": ("compat-api.txt", b"compat retrieval context knowledge evidence", "text/plain")},
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

        ingest = client.post(
            "/memory/ingest",
            headers=headers,
            json={
                "session_id": "compat-session",
                "kb_id": kb_id,
                "query": "请引用知识证据",
                "answer": "好的，我会引用知识证据。",
                "source": "bot",
                "source_refs": [source_path],
            },
        )
        assert ingest.status_code == 200

        legacy = client.post(
            "/retrieval-context",
            headers=headers,
            json={"session_id": "compat-session", "query": "knowledge evidence", "kb_ids": [kb_id], "top_k": 2},
        )
        assert legacy.status_code == 200

        neutral = client.post(
            "/retrieval/context",
            headers=headers,
            json={
                "query": "knowledge evidence",
                "conversation": {"session_id": "compat-session"},
                "scope": {"kb_ids": [kb_id]},
                "policy": {"top_k": 2},
            },
        )
        assert neutral.status_code == 200

        legacy_payload = legacy.json()
        neutral_payload = neutral.json()
        legacy_policy = legacy_payload["scores"]["applied_policy"]
        neutral_policy = neutral_payload["trace"]["applied_policy"]
        assert legacy_policy["top_k"] == neutral_policy["top_k"]
        assert legacy_policy["memory_top_k"] == neutral_policy["memory_top_k"]
        assert legacy_policy.get("token_budget") == neutral_policy.get("token_budget")
        assert legacy_policy["max_context_chars"] == neutral_policy["max_context_chars"]
        assert legacy_policy["include_knowledge"] == neutral_policy["include_knowledge"]
        assert legacy_policy["include_short_term"] == neutral_policy["include_short_term"]
        assert legacy_policy["include_long_term"] == neutral_policy["include_long_term"]
        assert legacy_payload["source_refs"] == neutral_payload["knowledge"]["source_refs"]
        assert len(legacy_payload["kb_blocks"]) == len(neutral_payload["knowledge"]["hits"])
        assert len(legacy_payload["short_term_memory_blocks"]) == len(neutral_payload["memory"]["short_term_hits"])
        assert len(legacy_payload["long_term_memory_blocks"]) == len(neutral_payload["memory"]["long_term_hits"])


def test_assemble_context_source_refs_follow_input_knowledge_hits():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        response = client.post(
            "/retrieval/assemble-context",
            headers=headers,
            json={
                "query": "knowledge evidence",
                "knowledge_hits": [
                    {
                        "chunk_id": 1,
                        "kb_id": 1,
                        "document_id": 11,
                        "source_path": "/source/a.txt",
                        "text": "a" * 600,
                        "score": 0.95,
                        "metadata": {},
                    },
                    {
                        "chunk_id": 2,
                        "kb_id": 1,
                        "document_id": 12,
                        "source_path": "/source/b.txt",
                        "text": "secondary evidence",
                        "score": 0.75,
                        "metadata": {},
                    },
                ],
                "retrieval_policy": {"max_context_chars": 120},
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["source_refs"] == ["/source/a.txt", "/source/b.txt"]
        assert payload["context_sections"][0]["source_refs"] == ["/source/a.txt"]
        assert payload["applied_policy"]["max_context_chars"] == 400


def test_memory_namespace_isolates_short_term_memory_without_bot_object():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post("/kbs", headers=headers, json={"name": "Namespace KB", "description": "namespace"}).json()
        kb_id = kb["id"]

        created_a = client.post(
            "/memory/ingest",
            headers=headers,
            json={
                "session_id": "shared-session",
                "memory_namespace": "robot-a",
                "kb_id": kb_id,
                "query": "机器人 A 偏好简洁回答",
                "answer": "A: 简洁回答",
                "source": "bot",
            },
        )
        assert created_a.status_code == 200

        created_b = client.post(
            "/memory/ingest",
            headers=headers,
            json={
                "session_id": "shared-session",
                "memory_namespace": "robot-b",
                "kb_id": kb_id,
                "query": "机器人 B 偏好详细解释",
                "answer": "B: 详细解释",
                "source": "bot",
            },
        )
        assert created_b.status_code == 200

        recall_a = client.post(
            "/retrieval/recall-memory",
            headers=headers,
            json={
                "query": "偏好",
                "session_id": "shared-session",
                "memory_namespace": "robot-a",
                "kb_ids": [kb_id],
                "retrieval_policy": {"memory_top_k": 5},
                "debug": True,
            },
        )
        assert recall_a.status_code == 200
        payload_a = recall_a.json()
        assert payload_a["short_term_hits"]
        assert all(item["metadata"]["memory_namespace"] == "robot-a" for item in payload_a["short_term_hits"])
        assert any("简洁回答" in item["content"] for item in payload_a["short_term_hits"])
        assert not any("详细解释" in item["content"] for item in payload_a["short_term_hits"])

        recall_b = client.post(
            "/retrieval/recall-memory",
            headers=headers,
            json={
                "query": "偏好",
                "session_id": "shared-session",
                "memory_namespace": "robot-b",
                "kb_ids": [kb_id],
                "retrieval_policy": {"memory_top_k": 5},
                "debug": True,
            },
        )
        assert recall_b.status_code == 200
        payload_b = recall_b.json()
        assert payload_b["short_term_hits"]
        assert all(item["metadata"]["memory_namespace"] == "robot-b" for item in payload_b["short_term_hits"])
        assert any("详细解释" in item["content"] for item in payload_b["short_term_hits"])
        assert not any("简洁回答" in item["content"] for item in payload_b["short_term_hits"])


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
            data={"target_dir": "/personal/cleanup"},
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
            data={"target_dir": "/personal/dir-scope"},
            files={"file": ("a.txt", b"first nested document", "text/plain")},
        )
        assert first_upload.status_code == 200

        second_upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/dir-scope/nested"},
            files={"file": ("b.txt", b"second nested document", "text/plain")},
        )
        assert second_upload.status_code == 200

        binding = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": "/personal/dir-scope", "scope_type": "directory"},
        )
        assert binding.status_code == 200
        assert binding.json()["last_imported_at"] is None

        import_task = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": ["/personal/dir-scope"]},
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
            json={"source_paths": ["/personal/dir-scope"]},
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
            data={"target_dir": "/personal/binding-tasks"},
            files={"file": ("alpha.txt", b"alpha binding task content", "text/plain")},
        )
        assert upload_a.status_code == 200

        upload_b = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": "/personal/binding-tasks"},
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
            json={"source_paths": ["/personal/dup", "/personal/dup/file.txt"]},
        )
        assert created.status_code == 200
        created_payload = created.json()
        assert created_payload["source_paths"] == ["/personal/dup"]

        duplicate = client.post(
            f"/kbs/{kb_id}/tasks/import",
            headers=headers,
            json={"source_paths": ["/personal/dup"]},
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
            data={"target_dir": "/personal/binding-validation"},
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
            data={"target_dir": "/personal/workbench"},
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
