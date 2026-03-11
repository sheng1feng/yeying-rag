from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from knowledge.core.settings import get_settings
from knowledge.services.warehouse_session import WarehouseSessionService


@dataclass
class WarehouseFileEntry:
    path: str
    name: str
    entry_type: str
    size: int = 0
    modified_at: datetime | None = None


class WarehouseGateway:
    def browse(self, wallet_address: str, path: str, access_token: str | None = None) -> list[WarehouseFileEntry]:
        raise NotImplementedError

    def upload_personal(self, wallet_address: str, target_dir: str, file_name: str, content: bytes, access_token: str | None = None) -> str:
        raise NotImplementedError

    def read_file(self, wallet_address: str, path: str, access_token: str | None = None) -> bytes:
        raise NotImplementedError


class MockWarehouseGateway(WarehouseGateway):
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_wallet_root(self, wallet_address: str) -> Path:
        normalized = wallet_address.lower()
        wallet_root = self.root / normalized
        (wallet_root / "personal").mkdir(parents=True, exist_ok=True)
        (wallet_root / "apps").mkdir(parents=True, exist_ok=True)
        return wallet_root

    def _resolve_path(self, wallet_address: str, path: str) -> Path:
        normalized = "/" + path.strip().lstrip("/")
        if normalized == "/":
            normalized = "/personal"
        wallet_root = self._resolve_wallet_root(wallet_address)
        target = (wallet_root / normalized.lstrip("/")).resolve()
        if wallet_root.resolve() not in target.parents and target != wallet_root.resolve():
            raise ValueError("path escapes wallet root")
        return target

    def browse(self, wallet_address: str, path: str, access_token: str | None = None) -> list[WarehouseFileEntry]:
        target = self._resolve_path(wallet_address, path)
        if not target.exists():
            return []
        if target.is_file():
            stat = target.stat()
            return [
                WarehouseFileEntry(
                    path=path,
                    name=target.name,
                    entry_type="file",
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime),
                )
            ]
        entries: list[WarehouseFileEntry] = []
        for child in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            stat = child.stat()
            rel = "/" + str(child.relative_to(self._resolve_wallet_root(wallet_address))).replace(os.sep, "/")
            entries.append(
                WarehouseFileEntry(
                    path=rel,
                    name=child.name,
                    entry_type="directory" if child.is_dir() else "file",
                    size=0 if child.is_dir() else stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime),
                )
            )
        return entries

    def upload_personal(self, wallet_address: str, target_dir: str, file_name: str, content: bytes, access_token: str | None = None) -> str:
        target_dir = target_dir.strip() or "/personal/uploads"
        if not target_dir.startswith("/personal"):
            raise ValueError("uploads are only allowed to personal")
        destination = self._resolve_path(wallet_address, target_dir) / file_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return "/" + str(destination.relative_to(self._resolve_wallet_root(wallet_address))).replace(os.sep, "/")

    def read_file(self, wallet_address: str, path: str, access_token: str | None = None) -> bytes:
        target = self._resolve_path(wallet_address, path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(path)
        return target.read_bytes()


class BoundTokenWarehouseGateway(WarehouseGateway):
    def __init__(self, base_url: str, webdav_prefix: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.webdav_prefix = "/" + webdav_prefix.strip().strip("/")

    def _headers(self, access_token: str | None) -> dict[str, str]:
        if not access_token:
            raise ValueError("warehouse access token is required")
        return {"Authorization": f"Bearer {access_token}"}

    def _ensure_directory(self, directory: str, access_token: str | None) -> None:
        normalized = "/" + directory.strip().strip("/")
        if normalized in {"", "/"}:
            return
        parts = normalized.strip("/").split("/")
        current = ""
        for part in parts:
            current += f"/{part}"
            response = httpx.request(
                "PROPFIND",
                self._dav_url(current),
                headers={**self._headers(access_token), "Depth": "0"},
                timeout=30.0,
            )
            if response.status_code in (200, 207):
                continue
            if response.status_code != 404:
                response.raise_for_status()
            mkcol = httpx.request(
                "MKCOL",
                self._dav_url(current),
                headers=self._headers(access_token),
                timeout=30.0,
            )
            if mkcol.status_code not in (201, 405):
                mkcol.raise_for_status()

    def _dav_url(self, path: str) -> str:
        path = "/" + path.strip().lstrip("/")
        return f"{self.base_url}{self.webdav_prefix}{quote(path)}"

    def browse(self, wallet_address: str, path: str, access_token: str | None = None) -> list[WarehouseFileEntry]:
        headers = self._headers(access_token)
        headers["Depth"] = "1"
        response = httpx.request("PROPFIND", self._dav_url(path), headers=headers, timeout=30.0)
        response.raise_for_status()
        return self._parse_propfind(path, response.text)

    def upload_personal(self, wallet_address: str, target_dir: str, file_name: str, content: bytes, access_token: str | None = None) -> str:
        target_dir = target_dir.strip() or "/personal/uploads"
        if not target_dir.startswith("/personal"):
            raise ValueError("uploads are only allowed to personal")
        self._ensure_directory(target_dir, access_token)
        target_path = f"{target_dir.rstrip('/')}/{file_name}"
        response = httpx.put(
            self._dav_url(target_path),
            headers=self._headers(access_token),
            content=content,
            timeout=120.0,
        )
        response.raise_for_status()
        return target_path

    def read_file(self, wallet_address: str, path: str, access_token: str | None = None) -> bytes:
        response = httpx.get(self._dav_url(path), headers=self._headers(access_token), timeout=120.0)
        response.raise_for_status()
        return response.content

    def _parse_propfind(self, requested_path: str, xml_payload: str) -> list[WarehouseFileEntry]:
        try:
            root = ElementTree.fromstring(xml_payload)
        except ElementTree.ParseError:
            return []

        ns = {
            "d": "DAV:",
        }
        entries: list[WarehouseFileEntry] = []
        for response in root.findall("d:response", ns):
            href = response.findtext("d:href", default="", namespaces=ns)
            prop = response.find("d:propstat/d:prop", ns)
            if prop is None:
                continue
            content_length = prop.findtext("d:getcontentlength", default="0", namespaces=ns)
            last_modified = prop.findtext("d:getlastmodified", default="", namespaces=ns)
            collection = prop.find("d:resourcetype/d:collection", ns)

            clean_href = href
            path = clean_href.replace(self.webdav_prefix, "", 1) if clean_href.startswith(self.webdav_prefix) else clean_href
            path = "/" + path.strip().lstrip("/")
            name = path.rstrip("/").split("/")[-1] if path.rstrip("/") else requested_path.rstrip("/").split("/")[-1]
            modified_at = None
            if last_modified:
                try:
                    modified_at = datetime.strptime(last_modified, "%a, %d %b %Y %H:%M:%S %Z")
                except ValueError:
                    modified_at = None
            entries.append(
                WarehouseFileEntry(
                    path=path,
                    name=name,
                    entry_type="directory" if collection is not None else "file",
                    size=int(content_length or 0),
                    modified_at=modified_at,
                )
            )
        normalized_requested = "/" + requested_path.strip().lstrip("/")
        if normalized_requested != "/":
            trimmed_requested = normalized_requested.rstrip("/")
            if len(entries) > 1:
                filtered = []
                for entry in entries:
                    entry_trimmed = entry.path.rstrip("/")
                    if entry_trimmed == trimmed_requested and entry.entry_type == "directory":
                        continue
                    filtered.append(entry)
                return filtered
        return entries


def build_warehouse_gateway() -> WarehouseGateway:
    settings = get_settings()
    if settings.warehouse_gateway_mode == "bound_token":
        return BoundTokenWarehouseGateway(
            base_url=settings.warehouse_base_url,
            webdav_prefix=settings.warehouse_webdav_prefix,
        )
    return MockWarehouseGateway(root=settings.warehouse_mock_root)
