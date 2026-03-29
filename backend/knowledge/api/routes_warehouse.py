from __future__ import annotations

from datetime import datetime
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.core.settings import get_settings
from knowledge.db.session import get_db
from knowledge.models import KnowledgeBase, SourceBinding, UploadRecord
from knowledge.schemas.warehouse import (
    SourceBindingCreateRequest,
    SourceBindingResponse,
    SourceBindingUpdateRequest,
    UploadResponse,
    WarehouseBrowseResponse,
    WarehouseBootstrapChallengeResponse,
    WarehouseBootstrapCleanupRequest,
    WarehouseBootstrapInitializeRequest,
    WarehouseBootstrapInitializeResponse,
    WarehouseProvisioningAttemptRead,
    WarehouseCredentialCreateRequest,
    WarehouseCredentialRevealResponse,
    WarehouseCredentialSummary,
    WarehouseEntry,
    WarehouseStatusResponse,
    WarehouseWriteCredentialResponse,
)
from knowledge.services.bindings import BindingService
from knowledge.services.filetypes import infer_file_type
from knowledge.services.parser import DocumentParser
from knowledge.services.warehouse import build_warehouse_gateway
from knowledge.services.warehouse_access import WarehouseAccessService
from knowledge.services.warehouse_bootstrap import WarehouseBootstrapError, WarehouseBootstrapExecutionError, WarehouseBootstrapService
from knowledge.services.warehouse_scope import ensure_current_app_path, warehouse_app_id, warehouse_app_root, warehouse_default_upload_dir


router = APIRouter(tags=["warehouse_sources"])
logger = logging.getLogger(__name__)
gateway = build_warehouse_gateway()
warehouse_access_service = WarehouseAccessService(warehouse_gateway=gateway)
warehouse_bootstrap_service = WarehouseBootstrapService(warehouse_gateway=gateway)
parser = DocumentParser()
binding_service = BindingService()
settings = get_settings()


def _ensure_current_app_path_or_400(path: str | None, label: str = "path") -> str:
    try:
        return ensure_current_app_path(path, label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _binding_summary_or_404(db: Session, kb: KnowledgeBase, binding_id: int) -> dict:
    summaries = binding_service.list_binding_summaries(db, kb)
    for item in summaries:
        if int(item["id"]) == int(binding_id):
            return item
    raise HTTPException(status_code=404, detail="binding not found")


def _raise_access_error(db: Session, resolved, exc: Exception) -> None:
    if resolved is not None and warehouse_access_service.is_auth_error(exc):
        warehouse_access_service.mark_access_invalid(resolved)
        db.commit()
    logger.exception("warehouse access error", extra={"credential_id": getattr(getattr(resolved, "credential", None), "id", None)})
    raise HTTPException(status_code=400, detail=_warehouse_error_detail(exc)) from exc


def _warehouse_error_detail(exc: Exception, path: str | None = None) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        target = path or str(exc.request.url)
        if status == 401:
            return (
                f"warehouse rejected the access key for {target}. "
                "This usually means the ak/sk is wrong, the key is revoked or expired, or the access key has no bound directories. "
                "In warehouse, creating an access key is not enough; you must also bind at least one directory before the key can be used."
            )
        if status == 403:
            return (
                f"warehouse authenticated the access key but denied access to {target}. "
                "Bind this directory or choose a root_path under an already bound directory, and ensure the key has the required permissions."
            )
    return str(exc)


def _current_app_binding_status(db: Session, wallet_address: str) -> WarehouseStatusResponse:
    read_credentials = warehouse_access_service.list_read_credentials(db, wallet_address)
    write_credential = warehouse_access_service.get_write_credential(db, wallet_address)
    credentials_ready = bool(read_credentials or write_credential is not None)
    return WarehouseStatusResponse(
        wallet_address=wallet_address,
        credentials_ready=credentials_ready,
        read_credentials_count=len(read_credentials),
        write_credential_id=write_credential.id if write_credential is not None else None,
        write_credential_status=write_credential.status if write_credential is not None else None,
        write_root_path=write_credential.root_path if write_credential is not None else None,
        current_app_id=warehouse_app_id(),
        current_app_root=warehouse_app_root(),
        current_app_upload_dir=warehouse_default_upload_dir(),
        warehouse_base_url=settings.warehouse_base_url if credentials_ready else None,
    )


def _warehouse_bootstrap_error_detail(exc: Exception) -> str:
    if isinstance(exc, WarehouseBootstrapError):
        if exc.status == 401:
            return (
                "warehouse 未接受当前钱包签名。请确认浏览器里签名的是当前 knowledge 登录钱包，"
                "并且 warehouse 侧允许该钱包地址完成登录。"
            )
        if exc.status == 403:
            return "warehouse 已识别当前钱包，但拒绝执行该初始化操作。请检查该账号在 warehouse 的目录写入权限。"
        if exc.status == 404:
            return "warehouse 未找到对应登录入口或钱包账号。请确认 warehouse 公网地址配置正确。"
        return str(exc)
    return str(exc)


def _write_credential_response(db: Session, wallet_address: str) -> WarehouseWriteCredentialResponse:
    credential = warehouse_access_service.get_write_credential(db, wallet_address)
    if credential is None:
        return WarehouseWriteCredentialResponse(configured=False, credential=None)
    return WarehouseWriteCredentialResponse(
        configured=True,
        credential=WarehouseCredentialSummary.model_validate(warehouse_access_service.summarize(credential)),
    )


@router.get("/warehouse/status", response_model=WarehouseStatusResponse)
def warehouse_status(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> WarehouseStatusResponse:
    return _current_app_binding_status(db, wallet_address)


@router.post("/warehouse/bootstrap/challenge", response_model=WarehouseBootstrapChallengeResponse)
def warehouse_bootstrap_challenge(wallet_address: str = Depends(get_current_wallet)) -> WarehouseBootstrapChallengeResponse:
    try:
        payload = warehouse_bootstrap_service.request_challenge(wallet_address)
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed to request warehouse bootstrap challenge", extra={"wallet_address": wallet_address})
        raise HTTPException(status_code=400, detail=_warehouse_bootstrap_error_detail(exc)) from exc
    return WarehouseBootstrapChallengeResponse.model_validate(payload)


@router.post("/warehouse/bootstrap/initialize", response_model=WarehouseBootstrapInitializeResponse)
def warehouse_bootstrap_initialize(
    payload: WarehouseBootstrapInitializeRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseBootstrapInitializeResponse:
    try:
        result = warehouse_bootstrap_service.initialize_credentials(
            db,
            wallet_address=wallet_address,
            signature=payload.signature,
            mode=payload.mode,
            warehouse_access_service=warehouse_access_service,
        )
    except WarehouseBootstrapExecutionError as exc:
        logger.exception(
            "warehouse bootstrap completed with recoverable provisioning failure",
            extra={"wallet_address": wallet_address, "mode": payload.mode},
        )
        payload = jsonable_encoder(exc.payload)
        if payload.get("status") == "partial_success":
            return WarehouseBootstrapInitializeResponse.model_validate(payload)
        raise HTTPException(status_code=400, detail=payload) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "failed to initialize warehouse bootstrap credentials",
            extra={"wallet_address": wallet_address, "mode": payload.mode},
        )
        raise HTTPException(status_code=400, detail=_warehouse_bootstrap_error_detail(exc)) from exc
    return WarehouseBootstrapInitializeResponse.model_validate(result)


@router.get("/warehouse/bootstrap/attempts", response_model=list[WarehouseProvisioningAttemptRead])
def list_warehouse_bootstrap_attempts(
    limit: int = Query(default=20),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> list[WarehouseProvisioningAttemptRead]:
    attempts = warehouse_bootstrap_service.list_attempts(db, wallet_address, limit=limit)
    return [WarehouseProvisioningAttemptRead.model_validate(warehouse_bootstrap_service.summarize_attempt(attempt)) for attempt in attempts]


@router.get("/warehouse/bootstrap/attempts/{attempt_id}", response_model=WarehouseProvisioningAttemptRead)
def get_warehouse_bootstrap_attempt(
    attempt_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseProvisioningAttemptRead:
    try:
        attempt = warehouse_bootstrap_service.get_attempt_or_404(db, wallet_address, attempt_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WarehouseProvisioningAttemptRead.model_validate(warehouse_bootstrap_service.summarize_attempt(attempt))


@router.post("/warehouse/bootstrap/attempts/{attempt_id}/cleanup", response_model=WarehouseProvisioningAttemptRead)
def request_warehouse_bootstrap_attempt_cleanup(
    attempt_id: int,
    payload: WarehouseBootstrapCleanupRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseProvisioningAttemptRead:
    try:
        attempt = warehouse_bootstrap_service.request_cleanup(db, wallet_address, attempt_id, signature=payload.signature)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=_warehouse_bootstrap_error_detail(exc)) from exc
    return WarehouseProvisioningAttemptRead.model_validate(warehouse_bootstrap_service.summarize_attempt(attempt))


@router.get("/warehouse/credentials/read", response_model=list[WarehouseCredentialSummary])
def list_read_credentials(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> list[WarehouseCredentialSummary]:
    return [
        WarehouseCredentialSummary.model_validate(warehouse_access_service.summarize(credential))
        for credential in warehouse_access_service.list_read_credentials(db, wallet_address)
    ]


@router.post("/warehouse/credentials/read", response_model=WarehouseCredentialSummary)
def create_read_credential(
    payload: WarehouseCredentialCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseCredentialSummary:
    try:
        credential = warehouse_access_service.create_read_credential(
            db,
            wallet_address=wallet_address,
            key_id=payload.key_id,
            key_secret=payload.key_secret,
            root_path=payload.root_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "failed to create read warehouse credential",
            extra={"wallet_address": wallet_address, "root_path": payload.root_path, "key_id": payload.key_id},
        )
        raise HTTPException(status_code=400, detail=_warehouse_error_detail(exc, payload.root_path)) from exc
    return WarehouseCredentialSummary.model_validate(warehouse_access_service.summarize(credential))


@router.get("/warehouse/credentials/read/{credential_id}/secret", response_model=WarehouseCredentialRevealResponse)
def reveal_read_credential_secret(
    credential_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseCredentialRevealResponse:
    try:
        credential, secret = warehouse_access_service.reveal_secret(db, wallet_address, credential_id, "read")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WarehouseCredentialRevealResponse(
        id=credential.id,
        credential_kind=credential.credential_kind,
        key_id=credential.key_id,
        key_secret=secret,
    )


@router.delete("/warehouse/credentials/read/{credential_id}")
def delete_read_credential(
    credential_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> dict:
    try:
        warehouse_access_service.delete_read_credential(db, wallet_address, credential_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/warehouse/credentials/write", response_model=WarehouseWriteCredentialResponse)
def get_write_credential(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> WarehouseWriteCredentialResponse:
    return _write_credential_response(db, wallet_address)


@router.post("/warehouse/credentials/write", response_model=WarehouseCredentialSummary)
def upsert_write_credential(
    payload: WarehouseCredentialCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseCredentialSummary:
    try:
        credential = warehouse_access_service.upsert_write_credential(
            db,
            wallet_address=wallet_address,
            key_id=payload.key_id,
            key_secret=payload.key_secret,
            root_path=payload.root_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "failed to upsert write warehouse credential",
            extra={"wallet_address": wallet_address, "root_path": payload.root_path, "key_id": payload.key_id},
        )
        raise HTTPException(status_code=400, detail=_warehouse_error_detail(exc, payload.root_path)) from exc
    return WarehouseCredentialSummary.model_validate(warehouse_access_service.summarize(credential))


@router.get("/warehouse/credentials/write/secret", response_model=WarehouseCredentialRevealResponse)
def reveal_write_credential_secret(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> WarehouseCredentialRevealResponse:
    credential = warehouse_access_service.get_write_credential(db, wallet_address)
    if credential is None:
        raise HTTPException(status_code=404, detail="warehouse write credential not configured")
    _, secret = warehouse_access_service.reveal_secret(db, wallet_address, credential.id, credential.credential_kind)
    return WarehouseCredentialRevealResponse(
        id=credential.id,
        credential_kind=credential.credential_kind,
        key_id=credential.key_id,
        key_secret=secret,
    )


@router.delete("/warehouse/credentials/write")
def delete_write_credential(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    warehouse_access_service.delete_write_credential(db, wallet_address)
    return {"ok": True}


@router.get("/warehouse/browse", response_model=WarehouseBrowseResponse)
def browse_warehouse(
    path: str = warehouse_app_root(),
    credential_id: int | None = Query(default=None),
    use_write_credential: bool = Query(default=False),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseBrowseResponse:
    normalized_path = _ensure_current_app_path_or_400(path or warehouse_app_root())
    try:
        resolved = warehouse_access_service.resolve_browse_access(
            db,
            wallet_address,
            normalized_path,
            credential_id=credential_id,
            use_write_credential=use_write_credential,
        )
        entries = gateway.browse(wallet_address, normalized_path, auth=resolved.auth)
        warehouse_access_service.mark_access_success(resolved)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        _raise_access_error(db, locals().get("resolved"), exc)
    return WarehouseBrowseResponse(
        wallet_address=wallet_address,
        path=normalized_path,
        credential_id=resolved.credential.id,
        credential_kind=resolved.credential.credential_kind,
        entries=[
            WarehouseEntry(
                path=entry.path,
                name=entry.name,
                entry_type=entry.entry_type,
                size=entry.size,
                modified_at=entry.modified_at,
            )
            for entry in entries
        ],
    )


@router.post("/warehouse/upload", response_model=UploadResponse)
async def upload_to_app_dir(
    file: UploadFile = File(...),
    target_dir: str = Form(warehouse_default_upload_dir()),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> UploadResponse:
    normalized_target_dir = _ensure_current_app_path_or_400(target_dir or warehouse_default_upload_dir(), "target_dir")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="file is empty")
    try:
        resolved = warehouse_access_service.resolve_write_access(db, wallet_address, normalized_target_dir)
        gateway.ensure_app_space(
            wallet_address,
            auth=resolved.auth,
            base_path=resolved.credential.root_path,
            target_path=normalized_target_dir,
        )
        warehouse_path = gateway.upload_file(wallet_address, normalized_target_dir, file.filename, content, auth=resolved.auth)
        warehouse_access_service.mark_access_success(resolved)
    except Exception as exc:  # noqa: BLE001
        _raise_access_error(db, locals().get("resolved"), exc)
    record = UploadRecord(
        owner_wallet_address=wallet_address,
        warehouse_target_path=warehouse_path,
        file_name=file.filename,
        size=len(content),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return UploadResponse(
        warehouse_path=warehouse_path,
        file_name=file.filename,
        size=len(content),
        uploaded_at=record.created_at,
    )


@router.get("/warehouse/uploads")
def list_upload_records(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> list[dict]:
    records = (
        db.query(UploadRecord)
        .filter(UploadRecord.owner_wallet_address == wallet_address)
        .order_by(UploadRecord.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": record.id,
            "warehouse_target_path": record.warehouse_target_path,
            "file_name": record.file_name,
            "size": record.size,
            "status": record.status,
            "created_at": record.created_at,
        }
        for record in records
    ]


@router.get("/warehouse/preview")
def preview_warehouse_file(
    path: str,
    credential_id: int | None = Query(default=None),
    use_write_credential: bool = Query(default=False),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> dict:
    normalized_path = _ensure_current_app_path_or_400(path)
    try:
        resolved = warehouse_access_service.resolve_browse_access(
            db,
            wallet_address,
            normalized_path,
            credential_id=credential_id,
            use_write_credential=use_write_credential,
        )
        entries = gateway.browse(wallet_address, normalized_path, auth=resolved.auth)
    except Exception as exc:  # noqa: BLE001
        _raise_access_error(db, locals().get("resolved"), exc)
    entry = None
    for item in entries:
        if item.path.rstrip("/") == normalized_path.rstrip("/"):
            entry = item
            break
    if entry is None and entries:
        entry = entries[0]
    if entry is None:
        raise HTTPException(status_code=404, detail="file not found")
    if entry.entry_type == "directory":
        raise HTTPException(status_code=400, detail="preview only supports files")
    try:
        content = gateway.read_file(wallet_address, entry.path, auth=resolved.auth)
        warehouse_access_service.mark_access_success(resolved)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        _raise_access_error(db, resolved, exc)
    file_type = infer_file_type(entry.name)
    parsed = parser.parse(entry.name, content)
    preview_text = parsed[:4000]
    return {
        "path": entry.path,
        "file_name": entry.name,
        "file_type": file_type,
        "size": entry.size,
        "modified_at": entry.modified_at,
        "credential_id": resolved.credential.id,
        "credential_kind": resolved.credential.credential_kind,
        "preview": preview_text,
    }


@router.post("/kbs/{kb_id}/bindings", response_model=SourceBindingResponse)
def create_binding(
    kb_id: int,
    payload: SourceBindingCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> SourceBindingResponse:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    normalized_source_path = _ensure_current_app_path_or_400(payload.source_path, "source_path")
    credential_id = int(payload.credential_id or 0) or None
    if credential_id is None:
        read_credentials = warehouse_access_service.list_read_credentials(db, wallet_address)
        if len(read_credentials) == 1:
            credential_id = read_credentials[0].id
        else:
            raise HTTPException(status_code=400, detail="credential_id is required when multiple read credentials exist")
    try:
        credential = warehouse_access_service.get_read_credential(db, wallet_address, credential_id)
        if not warehouse_access_service.credential_covers_path(credential, normalized_source_path):
            raise ValueError(f"source_path must be under credential root {credential.root_path}")
        resolved = warehouse_access_service.resolve_explicit_access(db, wallet_address, normalized_source_path, credential_id)
        exists, entry_type = warehouse_access_service.path_exists_with_auth(wallet_address, normalized_source_path, resolved.auth)
        if not exists:
            raise ValueError(f"warehouse path not accessible: {normalized_source_path}")
        requested_scope_type = str(payload.scope_type or "auto").strip().lower() or "auto"
        scope_type = entry_type if requested_scope_type == "auto" else requested_scope_type
        if scope_type == "file" and entry_type != "file":
            raise ValueError(f"source_path is not a file: {normalized_source_path}")
        if scope_type == "directory" and entry_type != "directory":
            raise ValueError(f"source_path is not a directory: {normalized_source_path}")
        warehouse_access_service.mark_access_success(resolved)
    except Exception as exc:  # noqa: BLE001
        _raise_access_error(db, locals().get("resolved"), exc)

    existing = db.scalar(
        select(SourceBinding)
        .where(SourceBinding.kb_id == kb_id)
        .where(SourceBinding.source_path == normalized_source_path)
    )
    if existing is None:
        existing = SourceBinding(
            kb_id=kb_id,
            source_path=normalized_source_path,
            scope_type=scope_type,
            credential_id=credential_id,
        )
        db.add(existing)
    else:
        existing.scope_type = scope_type
        existing.credential_id = credential_id
    db.commit()
    return SourceBindingResponse.model_validate(_binding_summary_or_404(db, kb, existing.id))


@router.delete("/kbs/{kb_id}/bindings/{binding_id}")
def delete_binding(
    kb_id: int,
    binding_id: int,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> dict:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    binding = db.get(SourceBinding, binding_id)
    if binding is None or binding.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="binding not found")
    db.delete(binding)
    db.commit()
    return {"ok": True}


@router.patch("/kbs/{kb_id}/bindings/{binding_id}", response_model=SourceBindingResponse)
def update_binding(
    kb_id: int,
    binding_id: int,
    payload: SourceBindingUpdateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> dict:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    binding = db.get(SourceBinding, binding_id)
    if binding is None or binding.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="binding not found")
    binding.enabled = bool(payload.enabled)
    db.commit()
    return SourceBindingResponse.model_validate(_binding_summary_or_404(db, kb, binding.id))


@router.get("/kbs/{kb_id}/bindings", response_model=list[SourceBindingResponse])
def list_bindings(kb_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> list[dict]:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    return binding_service.list_binding_summaries(db, kb)
