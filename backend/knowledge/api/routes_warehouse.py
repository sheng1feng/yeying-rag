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
    return _require_token_for_path(db, wallet_address, "/personal")


def _require_token_for_path(db: Session, wallet_address: str, path: str) -> str | None:
    if warehouse_session_service.settings.warehouse_gateway_mode != "bound_token":
        return None
    try:
        return warehouse_session_service.get_access_token_for_path(db, wallet_address, path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"warehouse binding required: {exc}") from exc


@router.get("/warehouse/auth/status", response_model=WarehouseBindingStatusResponse)
def warehouse_auth_status(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> WarehouseBindingStatusResponse:
    jwt = warehouse_session_service.get_binding(db, wallet_address)
    ucan = warehouse_session_service.get_ucan_binding(db, wallet_address)
    app_ucans = warehouse_session_service.list_app_ucan_bindings(db, wallet_address)
    if ucan is not None:
        return WarehouseBindingStatusResponse(
            wallet_address=wallet_address,
            bound=jwt is not None or ucan is not None,
            binding_type="jwt" if jwt is not None else "ucan",
            jwt_bound=jwt is not None,
            ucan_bound=True,
            app_ucan_apps=[item.app_id for item in app_ucans],
            warehouse_base_url=warehouse_session_service.settings.warehouse_base_url,
            access_expires_at=jwt.access_expires_at if jwt is not None else None,
            refresh_expires_at=jwt.refresh_expires_at if jwt is not None else None,
            ucan_expires_at=ucan.root_expires_at,
        )
    if jwt is None:
        return WarehouseBindingStatusResponse(wallet_address=wallet_address, bound=False, app_ucan_apps=[item.app_id for item in app_ucans])
    return WarehouseBindingStatusResponse(
        wallet_address=wallet_address,
        bound=True,
        binding_type="jwt",
        jwt_bound=True,
        ucan_bound=False,
        app_ucan_apps=[item.app_id for item in app_ucans],
        warehouse_base_url=jwt.warehouse_base_url,
        access_expires_at=jwt.access_expires_at,
        refresh_expires_at=jwt.refresh_expires_at,
    )


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
        credential = warehouse_session_service.verify_and_store(db, payload.wallet_address, payload.signature)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WarehouseBindingStatusResponse(
        wallet_address=wallet_address,
        bound=True,
        binding_type="jwt",
        warehouse_base_url=credential.warehouse_base_url,
        access_expires_at=credential.access_expires_at,
        refresh_expires_at=credential.refresh_expires_at,
    )


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
        credential = warehouse_session_service.verify_ucan_and_store(db, payload.wallet_address, payload.nonce, payload.signature)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    jwt = warehouse_session_service.get_binding(db, wallet_address)
    return WarehouseBindingStatusResponse(
        wallet_address=wallet_address,
        bound=True,
        binding_type="jwt" if jwt is not None else "ucan",
        jwt_bound=jwt is not None,
        ucan_bound=True,
        app_ucan_apps=[item.app_id for item in warehouse_session_service.list_app_ucan_bindings(db, wallet_address)],
        warehouse_base_url=warehouse_session_service.settings.warehouse_base_url,
        access_expires_at=jwt.access_expires_at if jwt is not None else None,
        refresh_expires_at=jwt.refresh_expires_at if jwt is not None else None,
        ucan_expires_at=credential.root_expires_at,
    )


@router.post("/warehouse/auth/apps/ucan/bootstrap", response_model=WarehouseUcanBootstrapResponse)
def warehouse_auth_app_ucan_bootstrap(
    payload: WarehouseAppUcanBootstrapRequest,
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseUcanBootstrapResponse:
    if payload.wallet_address.lower() != wallet_address:
        raise HTTPException(status_code=400, detail="wallet mismatch")
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
    try:
        credential = warehouse_session_service.verify_app_ucan_and_store(
            db,
            payload.wallet_address,
            payload.app_id,
            payload.nonce,
            payload.signature,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    jwt = warehouse_session_service.get_binding(db, wallet_address)
    return WarehouseBindingStatusResponse(
        wallet_address=wallet_address,
        bound=True,
        binding_type="jwt" if jwt is not None else "ucan",
        jwt_bound=jwt is not None,
        ucan_bound=warehouse_session_service.get_ucan_binding(db, wallet_address) is not None,
        app_ucan_apps=[item.app_id for item in warehouse_session_service.list_app_ucan_bindings(db, wallet_address)],
        warehouse_base_url=warehouse_session_service.settings.warehouse_base_url,
        access_expires_at=jwt.access_expires_at if jwt is not None else None,
        refresh_expires_at=jwt.refresh_expires_at if jwt is not None else None,
        ucan_expires_at=credential.root_expires_at,
    )


@router.delete("/warehouse/auth/binding")
def warehouse_auth_unbind(wallet_address: str = Depends(get_current_wallet), db: Session = Depends(get_db)) -> dict:
    warehouse_session_service.delete_binding(db, wallet_address)
    return {"ok": True}


@router.get("/warehouse/browse", response_model=WarehouseBrowseResponse)
def browse_warehouse(
    path: str = "/personal",
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> WarehouseBrowseResponse:
    access_token = _require_token_for_path(db, wallet_address, path)
    try:
        entries = gateway.browse(wallet_address, path, access_token=access_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WarehouseBrowseResponse(
        wallet_address=wallet_address,
        path=path,
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
async def upload_to_personal(
    file: UploadFile = File(...),
    target_dir: str = Form("/personal/uploads"),
    wallet_address: str = Depends(get_current_wallet),
    db: Session = Depends(get_db),
) -> UploadResponse:
    if not target_dir.startswith("/personal"):
        raise HTTPException(status_code=400, detail="uploads are only allowed to personal")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="file is empty")
    access_token = _require_token_for_path(db, wallet_address, target_dir)
    try:
        warehouse_path = gateway.upload_personal(wallet_address, target_dir, file.filename, content, access_token=access_token)
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
    access_token = _require_token_for_path(db, wallet_address, path)
    try:
        entries = gateway.browse(wallet_address, path, access_token=access_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    entry = None
    for item in entries:
        if item.path.rstrip("/") == path.rstrip("/"):
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
    if not (payload.source_path.startswith("/personal") or payload.source_path.startswith("/apps/")):
        raise HTTPException(status_code=400, detail="source path must be under personal or apps")
    existing = db.scalar(
        select(SourceBinding)
        .where(SourceBinding.kb_id == kb_id)
        .where(SourceBinding.source_path == payload.source_path)
    )
    if existing is not None:
        return existing
    binding = SourceBinding(kb_id=kb_id, source_path=payload.source_path, scope_type=payload.scope_type)
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
