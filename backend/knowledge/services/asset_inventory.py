from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from knowledge.core.settings import get_settings
from knowledge.services.filetypes import infer_file_type
from knowledge.services.warehouse import WarehouseFileEntry, WarehouseGateway, build_warehouse_gateway
from knowledge.services.warehouse_scope import ensure_current_app_path, normalize_warehouse_path, warehouse_app_root
from knowledge.services.warehouse_session import WarehouseSessionService


class SourcePathMissingError(Exception):
    pass


class SourceScopeMismatchError(Exception):
    pass


@dataclass
class AssetSnapshot:
    asset_path: str
    asset_name: str
    asset_type: str
    source_version: str


class AssetInventoryService:
    def __init__(
        self,
        warehouse_gateway: WarehouseGateway | None = None,
        warehouse_session_service: WarehouseSessionService | None = None,
    ) -> None:
        self.settings = get_settings()
        self.warehouse_gateway = warehouse_gateway or build_warehouse_gateway()
        self.warehouse_session_service = warehouse_session_service or WarehouseSessionService()

    def list_asset_snapshots(
        self,
        db: Session,
        wallet_address: str,
        source_path: str,
        scope_type: str,
    ) -> list[AssetSnapshot]:
        normalized_source_path = ensure_current_app_path(source_path, "source_path", self.settings)
        normalized_scope_type = str(scope_type or "directory").strip().lower() or "directory"
        exists, entry_type = self.path_exists(db, wallet_address, normalized_source_path)
        if not exists:
            raise SourcePathMissingError(normalized_source_path)
        if normalized_scope_type == "file" and entry_type != "file":
            raise SourceScopeMismatchError(f"source path {normalized_source_path} is not a file")
        if normalized_scope_type == "directory" and entry_type != "directory":
            raise SourceScopeMismatchError(f"source path {normalized_source_path} is not a directory")

        if normalized_scope_type == "file":
            file_entry = self._get_exact_entry(db, wallet_address, normalized_source_path)
            if file_entry is None or file_entry.entry_type != "file":
                raise SourcePathMissingError(normalized_source_path)
            return [self._snapshot_for_entry(file_entry)]

        entries = self._iter_files(db, wallet_address, normalized_source_path)
        return [self._snapshot_for_entry(entry) for entry in entries]

    def path_exists(self, db: Session, wallet_address: str, path: str) -> tuple[bool, str | None]:
        normalized_path = ensure_current_app_path(path, "path", self.settings)
        app_root = warehouse_app_root(self.settings)
        access_token = self._get_access_token_for_path_if_needed(db, wallet_address, normalized_path)
        self.warehouse_gateway.ensure_app_space(wallet_address, access_token=access_token)
        if normalized_path == app_root:
            return True, "directory"
        entry = self._get_exact_entry(db, wallet_address, normalized_path)
        if entry is None:
            return False, None
        return True, entry.entry_type

    def _iter_files(self, db: Session, wallet_address: str, source_path: str) -> list[WarehouseFileEntry]:
        access_token = self._get_access_token_for_path_if_needed(db, wallet_address, source_path)
        entries = self.warehouse_gateway.browse(wallet_address, source_path, access_token=access_token)
        if len(entries) == 1 and entries[0].path == source_path and entries[0].entry_type == "file":
            return entries
        files: list[WarehouseFileEntry] = []
        for entry in entries:
            if entry.entry_type == "file":
                files.append(entry)
                continue
            if entry.entry_type == "directory":
                files.extend(self._iter_files(db, wallet_address, entry.path))
        return files

    def _get_exact_entry(self, db: Session, wallet_address: str, path: str) -> WarehouseFileEntry | None:
        normalized_path = normalize_warehouse_path(path)
        parent = self._parent_path(normalized_path)
        access_token = self._get_access_token_for_path_if_needed(db, wallet_address, parent)
        entries = self.warehouse_gateway.browse(wallet_address, parent, access_token=access_token)
        for entry in entries:
            if normalize_warehouse_path(entry.path) == normalized_path:
                return entry
        if parent == normalized_path:
            access_token = self._get_access_token_for_path_if_needed(db, wallet_address, normalized_path)
            root_entries = self.warehouse_gateway.browse(wallet_address, normalized_path, access_token=access_token)
            for entry in root_entries:
                if normalize_warehouse_path(entry.path) == normalized_path:
                    return entry
        return None

    def _get_access_token_for_path_if_needed(self, db: Session, wallet_address: str, path: str) -> str | None:
        if self.settings.warehouse_gateway_mode == "bound_token":
            return self.warehouse_session_service.get_access_token_for_path(db, wallet_address, path)
        return None

    @staticmethod
    def _snapshot_for_entry(entry: WarehouseFileEntry) -> AssetSnapshot:
        return AssetSnapshot(
            asset_path=normalize_warehouse_path(entry.path),
            asset_name=entry.name,
            asset_type=infer_file_type(entry.name),
            source_version=entry.modified_at.isoformat() if entry.modified_at else "",
        )

    @staticmethod
    def _parent_path(path: str) -> str:
        normalized_path = normalize_warehouse_path(path)
        parent = str(PurePosixPath(normalized_path).parent)
        if parent == ".":
            return normalized_path
        return normalize_warehouse_path(parent)
