from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge.api.deps import get_current_wallet
from knowledge.db.session import get_db
from knowledge.models import KnowledgeBase, SourceBinding, UploadRecord
from knowledge.schemas.warehouse_auth import (
    WarehouseAppUcanBootstrapRequest,
    WarehouseAppUcanVerifyRequest,
    WarehouseAuthChallengeRequest,
    WarehouseAuthChallengeResponse,
    WarehouseBindingStatusResponse,
    WarehouseUcanBootstrapRequest,
    WarehouseUcanBootstrapResponse,
    WarehouseUcanVerifyRequest,
    WarehouseAuthVerifyRequest,
)
from knowledge.schemas.warehouse import (
    SourceBindingCreateRequest,
    SourceBindingUpdateRequest,
    SourceBindingResponse,
    UploadResponse,
    WarehouseBrowseResponse,
    WarehouseEntry,
)
from knowledge.services.bindings import BindingService
from knowledge.services.warehouse_scope import (
    ensure_current_app_path,
    warehouse_app_id,
    warehouse_app_root,
    warehouse_default_upload_dir,
)
from knowledge.services.warehouse import build_warehouse_gateway
from knowledge.services.filetypes import infer_file_type
from knowledge.services.parser import DocumentParser
from knowledge.services.warehouse_session import WarehouseSessionService


router = APIRouter(tags=["warehouse_sources"])
gateway = build_warehouse_gateway()
warehouse_session_service = WarehouseSessionService()
parser = DocumentParser()
binding_service = BindingService()


def _require_bound_token_if_needed(db: Session, wallet_address: str) -> str | None:
    return _require_token_for_path(db, wallet_address, warehouse_app_root())


def _require_token_for_path(db: Session, wallet_address: str, path: str) -> str | None:
    if warehouse_session_service.settings.warehouse_gateway_mode != "bound_token":
        return None
    try:
        return warehouse_session_service.get_access_token_for_path(db, wallet_address, path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"warehouse binding required: {exc}") from exc


def _ensure_current_app_path_or_400(path: str | None, label: str = "path") -> str:
    try:
        return ensure_current_app_path(path, label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _current_app_binding_status(db: Session, wallet_address: str) -> WarehouseBindingStatusResponse:
    jwt = warehouse_session_service.get_binding(db, wallet_address)
    ucan = warehouse_session_service.get_ucan_binding(db, wallet_address)
    app_ucans = warehouse_session_service.list_app_ucan_bindings(db, wallet_address)
    current_app = warehouse_app_id()
    current_app_binding = warehouse_session_service.get_app_ucan_binding(db, wallet_address, current_app)
    bound = jwt is not None or ucan is not None or current_app_binding is not None
    if jwt is not None:
        binding_type = "jwt"
    elif current_app_binding is not None:
        binding_type = "app_ucan"
    elif ucan is not None:
        binding_type = "ucan"
    else:
        binding_type = None
    return WarehouseBindingStatusResponse(
        wallet_address=wallet_address,
        bound=bound,
        app_bound=current_app_binding is not None,
        binding_type=binding_type,
        jwt_bound=jwt is not None,
        ucan_bound=ucan is not None,
        current_app_id=current_app,
        current_app_root=warehouse_app_root(),
        current_app_upload_dir=warehouse_default_upload_dir(),
        app_ucan_apps=[item.app_id for item in app_ucans],
        warehouse_base_url=(jwt.warehouse_base_url if jwt is not None else warehouse_session_service.settings.warehouse_base_url) if bound else None,
        access_expires_at=jwt.access_expires_at if jwt is not None else None,
        refresh_expires_at=jwt.refresh_expires_at if jwt is not None else None,
        ucan_expires_at=(
            current_app_binding.root_expires_at
            if current_app_binding is not None
            else (ucan.root_expires_at if ucan is not None else None)
        ),
    )


@router.get("/warehouse/auth/status", response_model=WarehouseBindingStatusResponse)
def warehouse_auth_status(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> WarehouseBindingStatusResponse:
    return _current_app_binding_status(db, wallet_address)


@router.post("/warehouse/auth/challenge", response_model=WarehouseAuthChallengeResponse)
def warehouse_auth_challenge(
    payload: WarehouseAuthChallengeRequest,
    wallet_address: str = Depends(get_current_wallet),
) -> WarehouseAuthChallengeResponse:
    if payload.wallet_address.lower() != wallet_address:
        raise HTTPException(status_code=400, detail="wallet mismatch")
    try:
        challenge = warehouse_session_service.create_challenge(payload.wallet_address)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WarehouseAuthChallengeResponse(**challenge)


@router.post("/warehouse/auth/verify", response_model=WarehouseBindingStatusResponse)
def warehouse_auth_verify(
    payload: WarehouseAuthVerifyRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseBindingStatusResponse:
    if payload.wallet_address.lower() != wallet_address:
        raise HTTPException(status_code=400, detail="wallet mismatch")
    try:
        warehouse_session_service.verify_and_store(db, payload.wallet_address, payload.signature)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _current_app_binding_status(db, wallet_address)


@router.post("/warehouse/auth/ucan/bootstrap", response_model=WarehouseUcanBootstrapResponse)
def warehouse_auth_ucan_bootstrap(
    payload: WarehouseUcanBootstrapRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseUcanBootstrapResponse:
    if payload.wallet_address.lower() != wallet_address:
        raise HTTPException(status_code=400, detail="wallet mismatch")
    bootstrap = warehouse_session_service.create_ucan_bootstrap(db, payload.wallet_address)
    return WarehouseUcanBootstrapResponse(
        wallet_address=wallet_address,
        nonce=bootstrap.nonce,
        message=bootstrap.message,
        audience=bootstrap.audience,
        capability=bootstrap.cap_json,
        root_expires_at=bootstrap.root_expires_at,
        expires_at=bootstrap.expires_at,
    )


@router.post("/warehouse/auth/ucan/verify", response_model=WarehouseBindingStatusResponse)
def warehouse_auth_ucan_verify(
    payload: WarehouseUcanVerifyRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseBindingStatusResponse:
    if payload.wallet_address.lower() != wallet_address:
        raise HTTPException(status_code=400, detail="wallet mismatch")
    try:
        warehouse_session_service.verify_ucan_and_store(db, payload.wallet_address, payload.nonce, payload.signature)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _current_app_binding_status(db, wallet_address)


@router.post("/warehouse/auth/apps/ucan/bootstrap", response_model=WarehouseUcanBootstrapResponse)
def warehouse_auth_app_ucan_bootstrap(
    payload: WarehouseAppUcanBootstrapRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseUcanBootstrapResponse:
    if payload.wallet_address.lower() != wallet_address:
        raise HTTPException(status_code=400, detail="wallet mismatch")
    if payload.app_id != warehouse_app_id():
        raise HTTPException(status_code=400, detail=f"app_id must be {warehouse_app_id()}")
    bootstrap = warehouse_session_service.create_app_ucan_bootstrap(db, payload.wallet_address, payload.app_id, payload.action)
    return WarehouseUcanBootstrapResponse(
        wallet_address=wallet_address,
        nonce=bootstrap.nonce,
        message=bootstrap.message,
        audience=bootstrap.audience,
        capability=bootstrap.cap_json,
        root_expires_at=bootstrap.root_expires_at,
        expires_at=bootstrap.expires_at,
    )


@router.post("/warehouse/auth/apps/ucan/verify", response_model=WarehouseBindingStatusResponse)
def warehouse_auth_app_ucan_verify(
    payload: WarehouseAppUcanVerifyRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseBindingStatusResponse:
    if payload.wallet_address.lower() != wallet_address:
        raise HTTPException(status_code=400, detail="wallet mismatch")
    if payload.app_id != warehouse_app_id():
        raise HTTPException(status_code=400, detail=f"app_id must be {warehouse_app_id()}")
    try:
        warehouse_session_service.verify_app_ucan_and_store(
            db,
            payload.wallet_address,
            payload.app_id,
            payload.nonce,
            payload.signature,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _current_app_binding_status(db, wallet_address)


@router.delete("/warehouse/auth/binding")
def warehouse_auth_unbind(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    warehouse_session_service.delete_binding(db, wallet_address)
    return {"ok": True}


@router.get("/warehouse/browse", response_model=WarehouseBrowseResponse)
def browse_warehouse(
    path: str = warehouse_app_root(),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseBrowseResponse:
    normalized_path = _ensure_current_app_path_or_400(path or warehouse_app_root())
    access_token = _require_token_for_path(db, wallet_address, normalized_path)
    gateway.ensure_app_space(wallet_address, access_token=access_token)
    try:
        entries = gateway.browse(wallet_address, normalized_path, access_token=access_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WarehouseBrowseResponse(
        wallet_address=wallet_address,
        path=normalized_path,
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
    access_token = _require_token_for_path(db, wallet_address, normalized_target_dir)
    gateway.ensure_app_space(wallet_address, access_token=access_token)
    try:
        warehouse_path = gateway.upload_file(wallet_address, normalized_target_dir, file.filename, content, access_token=access_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
def preview_warehouse_file(path: str, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    normalized_path = _ensure_current_app_path_or_400(path)
    access_token = _require_token_for_path(db, wallet_address, normalized_path)
    try:
        entries = gateway.browse(wallet_address, normalized_path, access_token=access_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    content = gateway.read_file(wallet_address, entry.path, access_token=access_token)
    file_type = infer_file_type(entry.name)
    parsed = parser.parse(entry.name, content)
    preview_text = parsed[:4000]
    return {
        "path": entry.path,
        "file_name": entry.name,
        "file_type": file_type,
        "size": entry.size,
        "modified_at": entry.modified_at,
        "preview": preview_text,
    }


@router.post("/kbs/{kb_id}/bindings", response_model=SourceBindingResponse)
def create_binding(
    kb_id: int,
    payload: SourceBindingCreateRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> SourceBinding:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    normalized_source_path = _ensure_current_app_path_or_400(payload.source_path, "source_path")
    existing = db.scalar(
        select(SourceBinding)
        .where(SourceBinding.kb_id == kb_id)
        .where(SourceBinding.source_path == normalized_source_path)
    )
    if existing is not None:
        return existing
    binding = SourceBinding(kb_id=kb_id, source_path=normalized_source_path, scope_type=payload.scope_type)
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return binding


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
    db.refresh(binding)
    summaries = binding_service.list_binding_summaries(db, kb)
    for item in summaries:
        if int(item["id"]) == binding.id:
            return item
    return SourceBindingResponse.model_validate(binding)


@router.get("/kbs/{kb_id}/bindings", response_model=list[SourceBindingResponse])
def list_bindings(kb_id: int, wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> list[dict]:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.owner_wallet_address != wallet_address:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    return binding_service.list_binding_summaries(db, kb)
