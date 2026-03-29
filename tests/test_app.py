from __future__ import annotations

import base64
from datetime import timedelta
from sqlalchemy.exc import OperationalError
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
import httpx

from tests.helpers import configure_warehouse_credentials

from knowledge.db.session import engine, session_scope
from knowledge.main import app
from knowledge.models import ImportTask, WarehouseAccessCredential, WarehouseProvisioningAttempt
from knowledge.api.routes_console import CONSOLE_ASSET_VERSION
from knowledge.core.settings import Settings
from knowledge.services.warehouse import BoundTokenWarehouseGateway, WarehouseFileEntry, WarehouseGateway, WarehouseRequestAuth
from knowledge.services.warehouse_access import WarehouseAccessService
from knowledge.services.warehouse_scope import warehouse_app_path, warehouse_app_root, warehouse_default_upload_dir
from knowledge.utils.time import utc_now
from knowledge.workers.runner import TaskHeartbeat, Worker


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


def test_console_loads_wallet_adapter_before_app():
    wallet_ref = f"/static/js/wallet.js?v={CONSOLE_ASSET_VERSION}"
    bridge_ref = f"/static/js/warehouse_bridge.js?v={CONSOLE_ASSET_VERSION}"
    app_ref = f"/static/js/app.js?v={CONSOLE_ASSET_VERSION}"
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="connect-wallet"' in response.text
        assert "warehouse_base_url:" in response.text
        assert "warehouse_webdav_prefix:" in response.text
        wallet_index = response.text.index(wallet_ref)
        bridge_index = response.text.index(bridge_ref)
        app_index = response.text.index(app_ref)
        assert wallet_index < app_index
        assert bridge_index < app_index

        wallet_script = client.get(wallet_ref)
        assert wallet_script.status_code == 200
        assert "window.KnowledgeWallet" in wallet_script.text

        bridge_script = client.get(bridge_ref)
        assert bridge_script.status_code == 200
        assert "window.KnowledgeWarehouseBridge" in bridge_script.text


def test_read_credential_accepts_directory_scoped_key_without_parent_access():
    class DirectoryScopedGateway(WarehouseGateway):
        def browse(self, wallet_address: str, path: str, auth=None) -> list[WarehouseFileEntry]:
            normalized = str(path).rstrip("/") or "/"
            if normalized == f"{APP_ROOT}/uploads":
                return [
                    WarehouseFileEntry(
                        path=f"{APP_ROOT}/uploads",
                        name="uploads",
                        entry_type="directory",
                    )
                ]
            request = httpx.Request("PROPFIND", f"https://webdav.yeying.pub/dav{normalized}")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

        def ensure_app_space(self, wallet_address: str, auth=None, **kwargs) -> None:
            return None

        def upload_file(self, wallet_address: str, target_dir: str, file_name: str, content: bytes, auth=None) -> str:
            raise NotImplementedError

        def read_file(self, wallet_address: str, path: str, auth=None) -> bytes:
            raise NotImplementedError

    account = Account.create()
    service = WarehouseAccessService(warehouse_gateway=DirectoryScopedGateway())
    with TestClient(app):
        with session_scope() as db:
            credential = service.create_read_credential(
                db,
                wallet_address=account.address,
                key_id="ak_test_directory_only",
                key_secret="sk_test_directory_only",
                root_path=f"{APP_ROOT}/uploads",
            )
            assert credential.root_path == f"{APP_ROOT}/uploads"
            assert credential.status == "active"


def test_write_credential_bootstraps_app_space_on_save():
    class BootstrapGateway(WarehouseGateway):
        def __init__(self) -> None:
            self.ensure_calls: list[tuple[str, str | None, str | None]] = []

        def browse(self, wallet_address: str, path: str, auth=None) -> list[WarehouseFileEntry]:
            normalized = str(path).rstrip("/") or "/"
            request = httpx.Request("PROPFIND", f"https://webdav.yeying.pub/dav{normalized}")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

        def ensure_app_space(self, wallet_address: str, auth=None, *, base_path=None, target_path=None) -> None:
            self.ensure_calls.append((wallet_address, base_path, target_path))

        def upload_file(self, wallet_address: str, target_dir: str, file_name: str, content: bytes, auth=None) -> str:
            raise NotImplementedError

        def read_file(self, wallet_address: str, path: str, auth=None) -> bytes:
            raise NotImplementedError

    account = Account.create()
    gateway = BootstrapGateway()
    service = WarehouseAccessService(warehouse_gateway=gateway)
    with TestClient(app):
        with session_scope() as db:
            credential = service.upsert_write_credential(
                db,
                wallet_address=account.address,
                key_id="ak_test_write_bootstrap",
                key_secret="sk_test_write_bootstrap",
                root_path=APP_ROOT,
            )
            assert credential.root_path == APP_ROOT
            assert credential.status == "active"
    assert gateway.ensure_calls == [(account.address, APP_ROOT, APP_ROOT)]


def test_bound_token_gateway_bootstrap_does_not_probe_apps_parent(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append((method, url))
        if method == "PROPFIND":
            return httpx.Response(404, request=httpx.Request(method, url))
        if method == "MKCOL":
            return httpx.Response(201, request=httpx.Request(method, url))
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(httpx, "request", fake_request)
    gateway = BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav")
    gateway.ensure_app_space(
        "0xabc",
        auth=WarehouseRequestAuth.basic("ak_test_write_bootstrap", "sk_test_write_bootstrap"),
        base_path=APP_ROOT,
        target_path=APP_ROOT,
    )

    urls = [url for _, url in calls]
    assert "https://webdav.yeying.pub/dav/apps" not in urls
    assert "https://webdav.yeying.pub/dav/apps/knowledge.yeying.pub" in urls


def test_bound_token_gateway_bootstrap_for_upload_scope_skips_unrelated_dirs(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append((method, url))
        if method == "PROPFIND":
            return httpx.Response(404, request=httpx.Request(method, url))
        if method == "MKCOL":
            return httpx.Response(201, request=httpx.Request(method, url))
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr(httpx, "request", fake_request)
    gateway = BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav")
    gateway.ensure_app_space(
        "0xabc",
        auth=WarehouseRequestAuth.basic("ak_test_write_bootstrap", "sk_test_write_bootstrap"),
        base_path=UPLOADS_ROOT,
        target_path=UPLOADS_ROOT,
    )

    urls = [url for _, url in calls]
    assert "https://webdav.yeying.pub/dav/apps" not in urls
    assert "https://webdav.yeying.pub/dav/apps/knowledge.yeying.pub/library" not in urls
    assert "https://webdav.yeying.pub/dav/apps/knowledge.yeying.pub/system" not in urls
    assert urls == [
        "https://webdav.yeying.pub/dav/apps/knowledge.yeying.pub/uploads",
        "https://webdav.yeying.pub/dav/apps/knowledge.yeying.pub/uploads",
    ]


def test_end_to_end_auth_upload_import_search():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)

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


def test_upload_succeeds_with_write_credential_scoped_to_uploads_subtree():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        write_response = client.post(
            "/warehouse/credentials/write",
            headers=headers,
            json={
                "key_id": "ak_test_write_uploads_only",
                "key_secret": "sk_test_write_uploads_only",
                "root_path": UPLOADS_ROOT,
            },
        )
        assert write_response.status_code == 200
        assert write_response.json()["root_path"] == UPLOADS_ROOT

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("uploads/scoped/nested")},
            files={"file": ("scoped.txt", b"uploads scoped write credential", "text/plain")},
        )
        assert upload.status_code == 200
        assert upload.json()["warehouse_path"] == _app_path("uploads/scoped/nested/scoped.txt")


def test_binding_auto_scope_resolves_directory_paths():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)

        kb = client.post("/kbs", headers=headers, json={"name": "Auto Binding KB", "description": "auto"}).json()
        kb_id = kb["id"]

        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("uploads/auto-binding")},
            files={"file": ("source.txt", b"auto scope binding", "text/plain")},
        )
        assert upload.status_code == 200

        bind = client.post(
            f"/kbs/{kb_id}/bindings",
            headers=headers,
            json={"source_path": _app_path("uploads/auto-binding"), "scope_type": "auto"},
        )
        assert bind.status_code == 200
        assert bind.json()["scope_type"] == "directory"


def test_backend_proxy_bootstrap_initializes_warehouse_without_browser_cors(monkeypatch):
    account = Account.create()
    created_paths: set[str] = set()
    access_key_counter = {"value": 0}
    requested_addresses: list[str] = []

    def fake_request(method: str, url: str, **kwargs):
        method = method.upper()
        if url.endswith("/api/v1/public/auth/challenge") and method == "POST":
            body = kwargs.get("json") or {}
            requested_addresses.append(str(body.get("address") or ""))
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": str(body.get("address") or "").lower(),
                        "challenge": "warehouse bootstrap challenge",
                        "nonce": "nonce-1",
                        "issuedAt": 1,
                        "expiresAt": 2,
                    },
                },
            )
        if url.endswith("/api/v1/public/auth/verify") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": account.address.lower(),
                        "token": "warehouse-bearer-token",
                    },
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/create") and method == "POST":
            access_key_counter["value"] += 1
            idx = access_key_counter["value"]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "id": f"uuid-{idx}",
                    "name": f"key-{idx}",
                    "keyId": f"ak_bootstrap_{idx}",
                    "keySecret": f"sk_bootstrap_{idx}",
                    "permissions": ["read", "create", "update"] if idx == 1 else ["read"],
                    "bindingPaths": [],
                    "status": "active",
                    "createdAt": "2026-03-24T00:00:00+08:00",
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/bind") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                content=b'{"message":"bound successfully"}',
            )

        target = url.replace("https://webdav.yeying.pub/dav", "", 1)
        auth_header = (kwargs.get("headers") or {}).get("Authorization", "")
        if method == "PROPFIND":
            if auth_header.startswith("Bearer "):
                status = 207 if target in created_paths else 404
                return httpx.Response(status, request=httpx.Request(method, url), text="")
            if auth_header.startswith("Basic "):
                status = 207 if target in created_paths else 404
                if status == 207:
                    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav{target}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection /></d:resourcetype>
        <d:getcontentlength>0</d:getcontentlength>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""
                    return httpx.Response(status, request=httpx.Request(method, url), text=xml)
                return httpx.Response(status, request=httpx.Request(method, url), text="")
        if method == "MKCOL":
            created_paths.add(target.rstrip("/") or "/")
            return httpx.Response(201, request=httpx.Request(method, url))

        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(httpx, "request", fake_request)

    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        challenge = client.post("/warehouse/bootstrap/challenge", headers=headers)
        assert challenge.status_code == 200
        message = encode_defunct(text=challenge.json()["challenge"])
        signed = Account.sign_message(message, account.key)

        initialize = client.post(
            "/warehouse/bootstrap/initialize",
            headers=headers,
            json={"mode": "uploads_bundle", "signature": signed.signature.hex()},
        )
        assert initialize.status_code == 200
        payload = initialize.json()
        assert payload["status"] == "succeeded"
        assert payload["stage"] == "completed"
        assert payload["attempt_id"] > 0
        assert payload["warnings"] == []
        assert payload["cleanup_status"] == "not_needed"
        assert payload["target_path"] == UPLOADS_ROOT
        assert payload["write_key_id"] == "ak_bootstrap_1"
        assert payload["read_key_id"] == "ak_bootstrap_2"
        assert requested_addresses == [account.address]

        write_credential = client.get("/warehouse/credentials/write", headers=headers)
        assert write_credential.status_code == 200
        assert write_credential.json()["credential"]["key_id"] == "ak_bootstrap_1"

        read_credentials = client.get("/warehouse/credentials/read", headers=headers)
        assert read_credentials.status_code == 200
        assert read_credentials.json()[0]["key_id"] == "ak_bootstrap_2"

        with session_scope() as db:
            attempt = db.get(WarehouseProvisioningAttempt, int(payload["attempt_id"]))
            assert attempt is not None
            assert attempt.status == "succeeded"
            assert attempt.stage == "completed"
            assert attempt.write_key_id == "ak_bootstrap_1"
            assert attempt.read_key_id == "ak_bootstrap_2"
            assert attempt.write_credential_id is not None
            assert attempt.read_credential_id is not None
            write_credential = db.get(WarehouseAccessCredential, int(attempt.write_credential_id))
            read_credential = db.get(WarehouseAccessCredential, int(attempt.read_credential_id))
            assert write_credential is not None
            assert read_credential is not None
            assert write_credential.credential_source == "bootstrap"
            assert read_credential.credential_source == "bootstrap"
            assert write_credential.upstream_access_key_id == "uuid-1"
            assert read_credential.upstream_access_key_id == "uuid-2"
            assert write_credential.provisioning_attempt_id == attempt.id
            assert read_credential.provisioning_attempt_id == attempt.id
            assert write_credential.provisioning_mode == "uploads_bundle"
            assert read_credential.provisioning_mode == "uploads_bundle"
            assert write_credential.remote_name == "key-1"
            assert read_credential.remote_name == "key-2"


def test_backend_proxy_bootstrap_returns_structured_partial_failure_when_read_credential_save_fails(monkeypatch):
    from knowledge.api import routes_warehouse
    from knowledge.services.warehouse_bootstrap import WarehouseBootstrapService

    account = Account.create()
    created_paths: set[str] = set()
    access_key_counter = {"value": 0}

    def _decode_basic_username(header_value: str) -> str:
        raw = header_value.removeprefix("Basic ").strip()
        decoded = base64.b64decode(raw).decode("utf-8")
        return decoded.split(":", 1)[0]

    def fake_request(method: str, url: str, **kwargs):
        method = method.upper()
        if url.endswith("/api/v1/public/auth/challenge") and method == "POST":
            body = kwargs.get("json") or {}
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": str(body.get("address") or "").lower(),
                        "challenge": "warehouse bootstrap challenge",
                        "nonce": "nonce-1",
                        "issuedAt": 1,
                        "expiresAt": 2,
                    },
                },
            )
        if url.endswith("/api/v1/public/auth/verify") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": account.address.lower(),
                        "token": "warehouse-bearer-token",
                    },
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/create") and method == "POST":
            access_key_counter["value"] += 1
            idx = access_key_counter["value"]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "id": f"uuid-{idx}",
                    "name": f"key-{idx}",
                    "keyId": f"ak_bootstrap_{idx}",
                    "keySecret": f"sk_bootstrap_{idx}",
                    "permissions": ["read", "create", "update"] if idx == 1 else ["read"],
                    "bindingPaths": [],
                    "status": "active",
                    "createdAt": "2026-03-24T00:00:00+08:00",
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/bind") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                content=b'{"message":"bound successfully"}',
            )

        target = url.replace("https://webdav.yeying.pub/dav", "", 1)
        auth_header = (kwargs.get("headers") or {}).get("Authorization", "")
        if method == "PROPFIND":
            if auth_header.startswith("Bearer "):
                status = 207 if target in created_paths else 404
                return httpx.Response(status, request=httpx.Request(method, url), text="")
            if auth_header.startswith("Basic "):
                username = _decode_basic_username(auth_header)
                if username == "ak_bootstrap_2":
                    request = httpx.Request(method, url)
                    response = httpx.Response(401, request=request, text="")
                    raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)
                status = 207 if target in created_paths else 404
                if status == 207:
                    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav{target}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection /></d:resourcetype>
        <d:getcontentlength>0</d:getcontentlength>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""
                    return httpx.Response(status, request=httpx.Request(method, url), text=xml)
                return httpx.Response(status, request=httpx.Request(method, url), text="")
        if method == "MKCOL":
            created_paths.add(target.rstrip("/") or "/")
            return httpx.Response(201, request=httpx.Request(method, url))

        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(httpx, "request", fake_request)
    bound_gateway = BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav")
    monkeypatch.setattr(routes_warehouse, "warehouse_access_service", WarehouseAccessService(warehouse_gateway=bound_gateway))
    monkeypatch.setattr(routes_warehouse, "warehouse_bootstrap_service", WarehouseBootstrapService(warehouse_gateway=bound_gateway))

    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        challenge = client.post("/warehouse/bootstrap/challenge", headers=headers)
        assert challenge.status_code == 200
        message = encode_defunct(text=challenge.json()["challenge"])
        signed = Account.sign_message(message, account.key)

        initialize = client.post(
            "/warehouse/bootstrap/initialize",
            headers=headers,
            json={"mode": "uploads_bundle", "signature": signed.signature.hex()},
        )
        assert initialize.status_code == 200
        detail = initialize.json()
        assert detail["status"] == "partial_success"
        assert detail["stage"] == "saving_read_credential"
        assert detail["attempt_id"] > 0
        assert detail["write_key_id"] == "ak_bootstrap_1"
        assert detail["read_key_id"] == "ak_bootstrap_2"
        assert detail["write_credential"]["key_id"] == "ak_bootstrap_1"
        assert detail["read_credential"] is None
        assert "warehouse rejected the access key" in detail["error_message"]
        assert detail["cleanup_status"] == "manual_cleanup_required"
        assert detail["warnings"]

        write_credential = client.get("/warehouse/credentials/write", headers=headers)
        assert write_credential.status_code == 200
        assert write_credential.json()["credential"]["key_id"] == "ak_bootstrap_1"

        read_credentials = client.get("/warehouse/credentials/read", headers=headers)
        assert read_credentials.status_code == 200
        assert read_credentials.json() == []

        with session_scope() as db:
            attempt = db.get(WarehouseProvisioningAttempt, int(detail["attempt_id"]))
            assert attempt is not None
            assert attempt.status == "partial_success"
            assert attempt.stage == "saving_read_credential"
            assert attempt.write_key_id == "ak_bootstrap_1"
            assert attempt.read_key_id == "ak_bootstrap_2"
            assert attempt.write_credential_id is not None
            assert attempt.read_credential_id is None


def test_backend_proxy_bootstrap_reuses_existing_local_bootstrap_credentials(monkeypatch):
    from knowledge.api import routes_warehouse
    from knowledge.services.warehouse_bootstrap import WarehouseBootstrapService

    account = Account.create()
    created_paths: set[str] = set()
    access_key_counter = {"value": 0}

    def fake_request(method: str, url: str, **kwargs):
        method = method.upper()
        if url.endswith("/api/v1/public/auth/challenge") and method == "POST":
            body = kwargs.get("json") or {}
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": str(body.get("address") or "").lower(),
                        "challenge": "warehouse bootstrap challenge",
                        "nonce": "nonce-1",
                        "issuedAt": 1,
                        "expiresAt": 2,
                    },
                },
            )
        if url.endswith("/api/v1/public/auth/verify") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": account.address.lower(),
                        "token": "warehouse-bearer-token",
                    },
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/create") and method == "POST":
            access_key_counter["value"] += 1
            idx = access_key_counter["value"]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "id": f"uuid-{idx}",
                    "name": f"key-{idx}",
                    "keyId": f"ak_bootstrap_{idx}",
                    "keySecret": f"sk_bootstrap_{idx}",
                    "permissions": ["read", "create", "update"] if idx == 1 else ["read"],
                    "bindingPaths": [],
                    "status": "active",
                    "createdAt": "2026-03-24T00:00:00+08:00",
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/bind") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                content=b'{"message":"bound successfully"}',
            )
        if url.endswith("/api/v1/public/webdav/access-keys/revoke") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                content=b'{"message":"revoked successfully"}',
            )

        target = url.replace("https://webdav.yeying.pub/dav", "", 1)
        auth_header = (kwargs.get("headers") or {}).get("Authorization", "")
        if method == "PROPFIND":
            status = 207 if target in created_paths else 404
            return httpx.Response(status, request=httpx.Request(method, url), text="" if auth_header.startswith("Bearer ") else f"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav{target}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection /></d:resourcetype>
        <d:getcontentlength>0</d:getcontentlength>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>""" if status == 207 else "")
        if method == "MKCOL":
            created_paths.add(target.rstrip("/") or "/")
            return httpx.Response(201, request=httpx.Request(method, url))

        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(httpx, "request", fake_request)
    bound_gateway = BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav")
    monkeypatch.setattr(routes_warehouse, "warehouse_access_service", WarehouseAccessService(warehouse_gateway=bound_gateway))
    monkeypatch.setattr(routes_warehouse, "warehouse_bootstrap_service", WarehouseBootstrapService(warehouse_gateway=bound_gateway))

    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        first_challenge = client.post("/warehouse/bootstrap/challenge", headers=headers)
        assert first_challenge.status_code == 200
        first_signed = Account.sign_message(encode_defunct(text=first_challenge.json()["challenge"]), account.key)
        first_initialize = client.post(
            "/warehouse/bootstrap/initialize",
            headers=headers,
            json={"mode": "uploads_bundle", "signature": first_signed.signature.hex()},
        )
        assert first_initialize.status_code == 200
        first_payload = first_initialize.json()
        assert first_payload["status"] == "succeeded"
        assert access_key_counter["value"] == 2

        second_challenge = client.post("/warehouse/bootstrap/challenge", headers=headers)
        assert second_challenge.status_code == 200
        second_signed = Account.sign_message(encode_defunct(text=second_challenge.json()["challenge"]), account.key)
        second_initialize = client.post(
            "/warehouse/bootstrap/initialize",
            headers=headers,
            json={"mode": "uploads_bundle", "signature": second_signed.signature.hex()},
        )
        assert second_initialize.status_code == 200
        second_payload = second_initialize.json()
        assert second_payload["status"] == "succeeded"
        assert second_payload["stage"] == "reused_local_credentials"
        assert second_payload["write_key_id"] == first_payload["write_key_id"]
        assert second_payload["read_key_id"] == first_payload["read_key_id"]
        assert "reused existing local bootstrap credentials" in second_payload["warnings"][0]
        assert access_key_counter["value"] == 2

        read_credentials = client.get("/warehouse/credentials/read", headers=headers)
        assert read_credentials.status_code == 200
        assert len(read_credentials.json()) == 1

        with session_scope() as db:
            attempts = list(
                db.query(WarehouseProvisioningAttempt)
                .filter(WarehouseProvisioningAttempt.owner_wallet_address == account.address.lower())
                .order_by(WarehouseProvisioningAttempt.id.asc())
                .all()
            )
            assert len(attempts) == 2
            assert attempts[1].stage == "reused_local_credentials"
            assert attempts[1].write_credential_id == attempts[0].write_credential_id
            assert attempts[1].read_credential_id == attempts[0].read_credential_id


def test_bootstrap_attempt_endpoints_list_and_get_current_wallet_attempts(monkeypatch):
    from knowledge.api import routes_warehouse
    from knowledge.services.warehouse_bootstrap import WarehouseBootstrapService

    account = Account.create()
    created_paths: set[str] = set()
    access_key_counter = {"value": 0}

    def fake_request(method: str, url: str, **kwargs):
        method = method.upper()
        if url.endswith("/api/v1/public/auth/challenge") and method == "POST":
            body = kwargs.get("json") or {}
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": str(body.get("address") or "").lower(),
                        "challenge": "warehouse bootstrap challenge",
                        "nonce": "nonce-1",
                        "issuedAt": 1,
                        "expiresAt": 2,
                    },
                },
            )
        if url.endswith("/api/v1/public/auth/verify") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": account.address.lower(),
                        "token": "warehouse-bearer-token",
                    },
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/create") and method == "POST":
            access_key_counter["value"] += 1
            idx = access_key_counter["value"]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "id": f"uuid-{idx}",
                    "name": f"key-{idx}",
                    "keyId": f"ak_bootstrap_{idx}",
                    "keySecret": f"sk_bootstrap_{idx}",
                    "permissions": ["read", "create", "update"] if idx == 1 else ["read"],
                    "bindingPaths": [],
                    "status": "active",
                    "createdAt": "2026-03-24T00:00:00+08:00",
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/bind") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                content=b'{"message":"bound successfully"}',
            )

        target = url.replace("https://webdav.yeying.pub/dav", "", 1)
        if method == "PROPFIND":
            auth_header = (kwargs.get("headers") or {}).get("Authorization", "")
            status = 207 if target in created_paths else 404
            if auth_header.startswith("Bearer "):
                return httpx.Response(status, request=httpx.Request(method, url), text="")
            if status == 207:
                xml = f"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav{target}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection /></d:resourcetype>
        <d:getcontentlength>0</d:getcontentlength>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""
                return httpx.Response(status, request=httpx.Request(method, url), text=xml)
            return httpx.Response(status, request=httpx.Request(method, url), text="")
        if method == "MKCOL":
            created_paths.add(target.rstrip("/") or "/")
            return httpx.Response(201, request=httpx.Request(method, url))

        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(httpx, "request", fake_request)
    bound_gateway = BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav")
    monkeypatch.setattr(routes_warehouse, "warehouse_access_service", WarehouseAccessService(warehouse_gateway=bound_gateway))
    monkeypatch.setattr(routes_warehouse, "warehouse_bootstrap_service", WarehouseBootstrapService(warehouse_gateway=bound_gateway))

    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        challenge = client.post("/warehouse/bootstrap/challenge", headers=headers)
        signed = Account.sign_message(encode_defunct(text=challenge.json()["challenge"]), account.key)
        initialize = client.post(
            "/warehouse/bootstrap/initialize",
            headers=headers,
            json={"mode": "uploads_bundle", "signature": signed.signature.hex()},
        )
        assert initialize.status_code == 200
        attempt_id = initialize.json()["attempt_id"]

        attempts = client.get("/warehouse/bootstrap/attempts", headers=headers)
        assert attempts.status_code == 200
        payload = attempts.json()
        assert payload
        assert payload[0]["id"] == attempt_id
        assert payload[0]["status"] == "succeeded"
        assert payload[0]["write_key_id"] == "ak_bootstrap_1"
        assert payload[0]["cleanup_status"] == "not_needed"

        attempt = client.get(f"/warehouse/bootstrap/attempts/{attempt_id}", headers=headers)
        assert attempt.status_code == 200
        detail = attempt.json()
        assert detail["id"] == attempt_id
        assert detail["stage"] == "completed"
        assert detail["details_json"]["write_credential_saved"] is True
        assert detail["details_json"]["read_credential_saved"] is True


def test_bootstrap_attempt_detail_is_wallet_scoped(monkeypatch):
    from knowledge.api import routes_warehouse
    from knowledge.services.warehouse_bootstrap import WarehouseBootstrapService

    account_a = Account.create()
    account_b = Account.create()
    created_paths: set[str] = set()
    access_key_counter = {"value": 0}

    def fake_request(method: str, url: str, **kwargs):
        method = method.upper()
        if url.endswith("/api/v1/public/auth/challenge") and method == "POST":
            body = kwargs.get("json") or {}
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": str(body.get("address") or "").lower(),
                        "challenge": "warehouse bootstrap challenge",
                        "nonce": "nonce-1",
                        "issuedAt": 1,
                        "expiresAt": 2,
                    },
                },
            )
        if url.endswith("/api/v1/public/auth/verify") and method == "POST":
            body = kwargs.get("json") or {}
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": str(body.get("address") or "").lower(),
                        "token": "warehouse-bearer-token",
                    },
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/create") and method == "POST":
            access_key_counter["value"] += 1
            idx = access_key_counter["value"]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "id": f"uuid-{idx}",
                    "name": f"key-{idx}",
                    "keyId": f"ak_bootstrap_{idx}",
                    "keySecret": f"sk_bootstrap_{idx}",
                    "permissions": ["read", "create", "update"] if idx % 2 == 1 else ["read"],
                    "bindingPaths": [],
                    "status": "active",
                    "createdAt": "2026-03-24T00:00:00+08:00",
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/bind") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                content=b'{"message":"bound successfully"}',
            )

        target = url.replace("https://webdav.yeying.pub/dav", "", 1)
        if method == "PROPFIND":
            auth_header = (kwargs.get("headers") or {}).get("Authorization", "")
            status = 207 if target in created_paths else 404
            if auth_header.startswith("Bearer "):
                return httpx.Response(status, request=httpx.Request(method, url), text="")
            if status == 207:
                xml = f"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav{target}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection /></d:resourcetype>
        <d:getcontentlength>0</d:getcontentlength>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""
                return httpx.Response(status, request=httpx.Request(method, url), text=xml)
            return httpx.Response(status, request=httpx.Request(method, url), text="")
        if method == "MKCOL":
            created_paths.add(target.rstrip("/") or "/")
            return httpx.Response(201, request=httpx.Request(method, url))

        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(httpx, "request", fake_request)
    bound_gateway = BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav")
    monkeypatch.setattr(routes_warehouse, "warehouse_access_service", WarehouseAccessService(warehouse_gateway=bound_gateway))
    monkeypatch.setattr(routes_warehouse, "warehouse_bootstrap_service", WarehouseBootstrapService(warehouse_gateway=bound_gateway))

    with TestClient(app) as client:
        token_a = _login(client, account_a)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        challenge_a = client.post("/warehouse/bootstrap/challenge", headers=headers_a)
        signed_a = Account.sign_message(encode_defunct(text=challenge_a.json()["challenge"]), account_a.key)
        initialize_a = client.post(
            "/warehouse/bootstrap/initialize",
            headers=headers_a,
            json={"mode": "uploads_bundle", "signature": signed_a.signature.hex()},
        )
        assert initialize_a.status_code == 200
        attempt_id = initialize_a.json()["attempt_id"]

        token_b = _login(client, account_b)
        headers_b = {"Authorization": f"Bearer {token_b}"}
        hidden = client.get(f"/warehouse/bootstrap/attempts/{attempt_id}", headers=headers_b)
        assert hidden.status_code == 404


def test_bootstrap_attempt_cleanup_marks_manual_cleanup_request(monkeypatch):
    from knowledge.api import routes_warehouse
    from knowledge.services.warehouse_bootstrap import WarehouseBootstrapService

    account = Account.create()
    created_paths: set[str] = set()
    access_key_counter = {"value": 0}

    def _decode_basic_username(header_value: str) -> str:
        raw = header_value.removeprefix("Basic ").strip()
        decoded = base64.b64decode(raw).decode("utf-8")
        return decoded.split(":", 1)[0]

    def fake_request(method: str, url: str, **kwargs):
        method = method.upper()
        if url.endswith("/api/v1/public/auth/challenge") and method == "POST":
            body = kwargs.get("json") or {}
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": str(body.get("address") or "").lower(),
                        "challenge": "warehouse bootstrap challenge",
                        "nonce": "nonce-1",
                        "issuedAt": 1,
                        "expiresAt": 2,
                    },
                },
            )
        if url.endswith("/api/v1/public/auth/verify") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": account.address.lower(),
                        "token": "warehouse-bearer-token",
                    },
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/create") and method == "POST":
            access_key_counter["value"] += 1
            idx = access_key_counter["value"]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "id": f"uuid-{idx}",
                    "name": f"key-{idx}",
                    "keyId": f"ak_bootstrap_{idx}",
                    "keySecret": f"sk_bootstrap_{idx}",
                    "permissions": ["read", "create", "update"] if idx == 1 else ["read"],
                    "bindingPaths": [],
                    "status": "active",
                    "createdAt": "2026-03-24T00:00:00+08:00",
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/bind") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                content=b'{"message":"bound successfully"}',
            )
        if url.endswith("/api/v1/public/webdav/access-keys/revoke") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                content=b'{"message":"revoked successfully"}',
            )

        target = url.replace("https://webdav.yeying.pub/dav", "", 1)
        auth_header = (kwargs.get("headers") or {}).get("Authorization", "")
        if method == "PROPFIND":
            if auth_header.startswith("Bearer "):
                status = 207 if target in created_paths else 404
                return httpx.Response(status, request=httpx.Request(method, url), text="")
            if auth_header.startswith("Basic "):
                username = _decode_basic_username(auth_header)
                if username == "ak_bootstrap_2":
                    request = httpx.Request(method, url)
                    response = httpx.Response(401, request=request, text="")
                    raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)
                status = 207 if target in created_paths else 404
                if status == 207:
                    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav{target}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection /></d:resourcetype>
        <d:getcontentlength>0</d:getcontentlength>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""
                    return httpx.Response(status, request=httpx.Request(method, url), text=xml)
                return httpx.Response(status, request=httpx.Request(method, url), text="")
        if method == "MKCOL":
            created_paths.add(target.rstrip("/") or "/")
            return httpx.Response(201, request=httpx.Request(method, url))

        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(httpx, "request", fake_request)
    bound_gateway = BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav")
    monkeypatch.setattr(routes_warehouse, "warehouse_access_service", WarehouseAccessService(warehouse_gateway=bound_gateway))
    monkeypatch.setattr(routes_warehouse, "warehouse_bootstrap_service", WarehouseBootstrapService(warehouse_gateway=bound_gateway))

    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}

        challenge = client.post("/warehouse/bootstrap/challenge", headers=headers)
        signed = Account.sign_message(encode_defunct(text=challenge.json()["challenge"]), account.key)
        initialize = client.post(
            "/warehouse/bootstrap/initialize",
            headers=headers,
            json={"mode": "uploads_bundle", "signature": signed.signature.hex()},
        )
        assert initialize.status_code == 200
        attempt_id = initialize.json()["attempt_id"]

        cleanup_challenge = client.post("/warehouse/bootstrap/challenge", headers=headers)
        cleanup_signed = Account.sign_message(encode_defunct(text=cleanup_challenge.json()["challenge"]), account.key)
        cleanup = client.post(
            f"/warehouse/bootstrap/attempts/{attempt_id}/cleanup",
            headers=headers,
            json={"signature": cleanup_signed.signature.hex()},
        )
        assert cleanup.status_code == 200
        payload = cleanup.json()
        assert payload["id"] == attempt_id
        assert payload["cleanup_status"] == "cleanup_completed"
        assert payload["details_json"]["cleanup_requested"] is True
        assert payload["details_json"]["cleanup_requested_at"]
        assert payload["details_json"]["cleanup_results"]["write"] == "revoked"
        assert payload["details_json"]["cleanup_results"]["read"] == "revoked"
        assert payload["stage"] == "cleanup_completed"

        with session_scope() as db:
            attempt = db.get(WarehouseProvisioningAttempt, int(attempt_id))
            assert attempt is not None
            assert attempt.details_json["cleanup_status"] == "cleanup_completed"
            write_credential = db.get(WarehouseAccessCredential, int(attempt.write_credential_id))
            assert write_credential is not None
            assert write_credential.status == "revoked_local"


def test_bootstrap_attempt_cleanup_is_wallet_scoped(monkeypatch):
    from knowledge.api import routes_warehouse
    from knowledge.services.warehouse_bootstrap import WarehouseBootstrapService

    account_a = Account.create()
    account_b = Account.create()
    created_paths: set[str] = set()
    access_key_counter = {"value": 0}

    def fake_request(method: str, url: str, **kwargs):
        method = method.upper()
        if url.endswith("/api/v1/public/auth/challenge") and method == "POST":
            body = kwargs.get("json") or {}
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": str(body.get("address") or "").lower(),
                        "challenge": "warehouse bootstrap challenge",
                        "nonce": "nonce-1",
                        "issuedAt": 1,
                        "expiresAt": 2,
                    },
                },
            )
        if url.endswith("/api/v1/public/auth/verify") and method == "POST":
            body = kwargs.get("json") or {}
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "address": str(body.get("address") or "").lower(),
                        "token": "warehouse-bearer-token",
                    },
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/create") and method == "POST":
            access_key_counter["value"] += 1
            idx = access_key_counter["value"]
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                json={
                    "id": f"uuid-{idx}",
                    "name": f"key-{idx}",
                    "keyId": f"ak_bootstrap_{idx}",
                    "keySecret": f"sk_bootstrap_{idx}",
                    "permissions": ["read", "create", "update"] if idx == 1 else ["read"],
                    "bindingPaths": [],
                    "status": "active",
                    "createdAt": "2026-03-24T00:00:00+08:00",
                },
            )
        if url.endswith("/api/v1/public/webdav/access-keys/bind") and method == "POST":
            return httpx.Response(
                200,
                request=httpx.Request(method, url),
                content=b'{"message":"bound successfully"}',
            )

        target = url.replace("https://webdav.yeying.pub/dav", "", 1)
        auth_header = (kwargs.get("headers") or {}).get("Authorization", "")
        if method == "PROPFIND":
            if auth_header.startswith("Bearer "):
                status = 207 if target in created_paths else 404
                return httpx.Response(status, request=httpx.Request(method, url), text="")
            status = 207 if target in created_paths else 404
            if status == 207:
                xml = f"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav{target}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection /></d:resourcetype>
        <d:getcontentlength>0</d:getcontentlength>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>"""
                return httpx.Response(status, request=httpx.Request(method, url), text=xml)
            return httpx.Response(status, request=httpx.Request(method, url), text="")
        if method == "MKCOL":
            created_paths.add(target.rstrip("/") or "/")
            return httpx.Response(201, request=httpx.Request(method, url))

        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(httpx, "request", fake_request)
    bound_gateway = BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav")
    monkeypatch.setattr(routes_warehouse, "warehouse_access_service", WarehouseAccessService(warehouse_gateway=bound_gateway))
    monkeypatch.setattr(routes_warehouse, "warehouse_bootstrap_service", WarehouseBootstrapService(warehouse_gateway=bound_gateway))

    with TestClient(app) as client:
        token_a = _login(client, account_a)
        headers_a = {"Authorization": f"Bearer {token_a}"}
        challenge = client.post("/warehouse/bootstrap/challenge", headers=headers_a)
        signed = Account.sign_message(encode_defunct(text=challenge.json()["challenge"]), account_a.key)
        initialize = client.post(
            "/warehouse/bootstrap/initialize",
            headers=headers_a,
            json={"mode": "app_root_write", "signature": signed.signature.hex()},
        )
        assert initialize.status_code == 200
        attempt_id = initialize.json()["attempt_id"]

        token_b = _login(client, account_b)
        headers_b = {"Authorization": f"Bearer {token_b}"}
        cleanup_challenge = client.post("/warehouse/bootstrap/challenge", headers=headers_b)
        cleanup_signed = Account.sign_message(encode_defunct(text=cleanup_challenge.json()["challenge"]), account_b.key)
        hidden = client.post(
            f"/warehouse/bootstrap/attempts/{attempt_id}/cleanup",
            headers=headers_b,
            json={"signature": cleanup_signed.signature.hex()},
        )
        assert hidden.status_code == 404


def test_bootstrap_service_uses_configured_name_prefix_and_expiry_policy():
    from types import SimpleNamespace
    from knowledge.services.warehouse_bootstrap import WarehouseBootstrapService

    class FakeAccessService:
        def find_reusable_bootstrap_write_credential(self, *args, **kwargs):
            return None

        def find_reusable_bootstrap_read_credential(self, *args, **kwargs):
            return None

        def upsert_write_credential(self, *args, **kwargs):
            return SimpleNamespace(id=101, key_id=kwargs["key_id"], root_path=kwargs["root_path"])

        def create_read_credential(self, *args, **kwargs):
            return SimpleNamespace(id=102, key_id=kwargs["key_id"], root_path=kwargs["root_path"])

        @staticmethod
        def summarize(credential):
            return {
                "id": credential.id,
                "credential_kind": "read_write" if credential.id == 101 else "read",
                "key_id": credential.key_id,
                "key_secret_masked": "masked",
                "root_path": credential.root_path,
                "status": "active",
                "last_verified_at": None,
                "last_used_at": None,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }

    settings = Settings(
        warehouse_bootstrap_key_name_prefix="yeying",
        warehouse_bootstrap_write_expires_value=30,
        warehouse_bootstrap_write_expires_unit="day",
        warehouse_bootstrap_read_expires_value=7,
        warehouse_bootstrap_read_expires_unit="hour",
    )
    bootstrap_service = WarehouseBootstrapService(
        settings=settings,
        warehouse_gateway=BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav"),
    )
    create_calls: list[dict[str, object]] = []
    bootstrap_service._verify_signature = lambda *_args, **_kwargs: "token"
    bootstrap_service._bind_access_key = lambda **_kwargs: None
    bootstrap_service._ensure_directory_chain = lambda **_kwargs: None

    def fake_create_access_key(*, token, name, permissions, expires_value, expires_unit):
        create_calls.append(
            {
                "token": token,
                "name": name,
                "permissions": list(permissions),
                "expires_value": expires_value,
                "expires_unit": expires_unit,
            }
        )
        idx = len(create_calls)
        return {
            "id": f"uuid-{idx}",
            "name": name,
            "keyId": f"ak_cfg_{idx}",
            "keySecret": f"sk_cfg_{idx}",
        }

    bootstrap_service._create_access_key = fake_create_access_key

    with session_scope() as db:
        payload = bootstrap_service.initialize_credentials(
            db,
            wallet_address=Account.create().address,
            signature="0xsig",
            mode="uploads_bundle",
            warehouse_access_service=FakeAccessService(),
        )

    assert payload["status"] == "succeeded"
    assert len(create_calls) == 2
    assert create_calls[0]["name"].startswith("yeying-uploads-write-")
    assert create_calls[0]["expires_value"] == 30
    assert create_calls[0]["expires_unit"] == "day"
    assert create_calls[1]["name"].startswith("yeying-uploads-read-")
    assert create_calls[1]["expires_value"] == 7
    assert create_calls[1]["expires_unit"] == "hour"


def test_bootstrap_service_can_disable_local_reuse_policy():
    from types import SimpleNamespace
    from knowledge.services.warehouse_bootstrap import WarehouseBootstrapService

    reusable_write = SimpleNamespace(
        id=201,
        key_id="ak_existing_write",
        root_path=UPLOADS_ROOT,
        upstream_access_key_id="uuid-existing-write",
        remote_name="existing-write",
    )
    reusable_read = SimpleNamespace(
        id=202,
        key_id="ak_existing_read",
        root_path=UPLOADS_ROOT,
        upstream_access_key_id="uuid-existing-read",
        remote_name="existing-read",
    )

    class FakeAccessService:
        def find_reusable_bootstrap_write_credential(self, *args, **kwargs):
            return reusable_write

        def find_reusable_bootstrap_read_credential(self, *args, **kwargs):
            return reusable_read

        def upsert_write_credential(self, *args, **kwargs):
            return SimpleNamespace(id=301, key_id=kwargs["key_id"], root_path=kwargs["root_path"])

        def create_read_credential(self, *args, **kwargs):
            return SimpleNamespace(id=302, key_id=kwargs["key_id"], root_path=kwargs["root_path"])

        @staticmethod
        def summarize(credential):
            return {
                "id": credential.id,
                "credential_kind": "read_write" if credential.id in {201, 301} else "read",
                "key_id": credential.key_id,
                "key_secret_masked": "masked",
                "root_path": credential.root_path,
                "status": "active",
                "last_verified_at": None,
                "last_used_at": None,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }

    settings = Settings(warehouse_bootstrap_enable_reuse=False)
    bootstrap_service = WarehouseBootstrapService(
        settings=settings,
        warehouse_gateway=BoundTokenWarehouseGateway(base_url="https://webdav.yeying.pub", webdav_prefix="/dav"),
    )
    bootstrap_service._verify_signature = lambda *_args, **_kwargs: "token"
    bootstrap_service._bind_access_key = lambda **_kwargs: None
    bootstrap_service._ensure_directory_chain = lambda **_kwargs: None
    create_calls: list[dict[str, object]] = []

    def fake_create_access_key(*, token, name, permissions, expires_value, expires_unit):
        create_calls.append({"name": name, "expires_value": expires_value, "expires_unit": expires_unit})
        idx = len(create_calls)
        return {"id": f"uuid-{idx}", "name": name, "keyId": f"ak_new_{idx}", "keySecret": f"sk_new_{idx}"}

    bootstrap_service._create_access_key = fake_create_access_key

    with session_scope() as db:
        payload = bootstrap_service.initialize_credentials(
            db,
            wallet_address=Account.create().address,
            signature="0xsig",
            mode="uploads_bundle",
            warehouse_access_service=FakeAccessService(),
        )

    assert payload["status"] == "succeeded"
    assert payload["stage"] == "completed"
    assert len(create_calls) == 2
    assert payload["write_key_id"] == "ak_new_1"
    assert payload["read_key_id"] == "ak_new_2"


def test_warehouse_app_only_paths_reject_personal_scope():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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


def test_worker_disables_background_heartbeat_for_sqlite():
    worker = Worker()
    assert worker._use_background_heartbeat() is False


def test_task_heartbeat_treats_sqlite_lock_as_transient():
    exc = OperationalError(
        statement="UPDATE import_tasks SET heartbeat_at=?",
        params=(),
        orig=Exception("database is locked"),
    )
    assert TaskHeartbeat._is_transient_lock_error(exc) is True


def test_cancel_pending_task_marks_canceled_without_processing():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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


def test_kb_config_update_rebuilds_existing_source_evidence_with_latest_chunking():
    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)

        kb = client.post(
            "/kbs",
            headers=headers,
            json={
                "name": "Evidence Config KB",
                "description": "evidence-config",
                "retrieval_config": {
                    "chunk_size": 200,
                    "chunk_overlap": 0,
                    "retrieval_top_k": 4,
                    "memory_top_k": 3,
                    "embedding_model": "text-embedding-3-small",
                },
            },
        )
        assert kb.status_code == 200
        kb_id = kb.json()["id"]

        content = ("evidence rebuild after config update " * 60).encode()
        upload = client.post(
            "/warehouse/upload",
            headers=headers,
            data={"target_dir": _app_path("evidence-config")},
            files={"file": ("evidence.txt", content, "text/plain")},
        )
        assert upload.status_code == 200

        source = client.post(
            f"/kbs/{kb_id}/sources",
            headers=headers,
            json={"source_type": "warehouse", "source_path": _app_path("evidence-config"), "scope_type": "directory"},
        )
        assert source.status_code == 200
        source_id = source.json()["id"]

        scan = client.post(f"/kbs/{kb_id}/sources/{source_id}/scan", headers=headers)
        assert scan.status_code == 200
        build = client.post(f"/kbs/{kb_id}/sources/{source_id}/build-evidence", headers=headers)
        assert build.status_code == 200

        before_evidence = client.get(f"/kbs/{kb_id}/evidence", headers=headers, params={"source_id": source_id})
        assert before_evidence.status_code == 200
        before_payload = before_evidence.json()
        assert before_payload

        updated = client.patch(
            f"/kbs/{kb_id}",
            headers=headers,
            json={"retrieval_config": {"chunk_size": 60, "chunk_overlap": 0}},
        )
        assert updated.status_code == 200

        after_evidence = client.get(f"/kbs/{kb_id}/evidence", headers=headers, params={"source_id": source_id})
        assert after_evidence.status_code == 200
        after_payload = after_evidence.json()
        assert len(after_payload) > len(before_payload)
        assert all(item["vector_status"] == "indexed" for item in after_payload)


def test_ops_endpoints_require_auth():
    with TestClient(app) as client:
        overview = client.get("/ops/overview")
        assert overview.status_code == 401

        account = Account.create()
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)

        authed_overview = client.get("/ops/overview", headers=headers)
        assert authed_overview.status_code == 200
        assert "knowledge_bases" in authed_overview.json()


def test_delete_document_route_cleans_vector_store(monkeypatch):
    from knowledge.api import routes_documents

    account = Account.create()
    with TestClient(app) as client:
        token = _login(client, account)
        headers = {"Authorization": f"Bearer {token}"}
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
        configure_warehouse_credentials(client, headers)

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
