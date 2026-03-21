from __future__ import annotations

from knowledge.core.settings import Settings, get_settings


WAREHOUSE_APP_SUBDIRECTORIES = ("uploads", "staging", "library", "exports", "system")


def _settings(settings: Settings | None = None) -> Settings:
    return settings or get_settings()


def normalize_warehouse_path(path: str | None, default: str = "/") -> str:
    raw = str(path if path is not None else default).strip() or default
    normalized = "/" + raw.lstrip("/")
    if normalized != "/":
        normalized = normalized.rstrip("/")
    return normalized or "/"


def warehouse_apps_prefix(settings: Settings | None = None) -> str:
    current = _settings(settings)
    return normalize_warehouse_path(current.warehouse_apps_prefix, "/apps")


def warehouse_app_id(settings: Settings | None = None) -> str:
    current = _settings(settings)
    candidate = str(current.warehouse_app_id or current.app_name).strip()
    if not candidate:
        raise ValueError("warehouse app id is required")
    return candidate


def warehouse_app_root(settings: Settings | None = None) -> str:
    return f"{warehouse_apps_prefix(settings)}/{warehouse_app_id(settings)}"


def warehouse_app_path(relative_path: str = "", settings: Settings | None = None) -> str:
    root = warehouse_app_root(settings)
    suffix = str(relative_path or "").strip().strip("/")
    return root if not suffix else f"{root}/{suffix}"


def warehouse_default_upload_dir(settings: Settings | None = None) -> str:
    return warehouse_app_path("uploads", settings)


def warehouse_app_directories(settings: Settings | None = None) -> list[str]:
    current = _settings(settings)
    return [warehouse_app_root(current), *(warehouse_app_path(name, current) for name in WAREHOUSE_APP_SUBDIRECTORIES)]


def is_current_app_path(path: str | None, settings: Settings | None = None) -> bool:
    current = _settings(settings)
    normalized = normalize_warehouse_path(path, warehouse_app_root(current))
    root = warehouse_app_root(current)
    return normalized == root or normalized.startswith(f"{root}/")


def ensure_current_app_path(path: str | None, label: str = "path", settings: Settings | None = None) -> str:
    current = _settings(settings)
    normalized = normalize_warehouse_path(path, warehouse_app_root(current))
    if not is_current_app_path(normalized, current):
        raise ValueError(f"{label} must be under {warehouse_app_root(current)}")
    return normalized


def extract_app_id_from_path(path: str | None, settings: Settings | None = None) -> str | None:
    current = _settings(settings)
    normalized = normalize_warehouse_path(path)
    prefix = warehouse_apps_prefix(current)
    if normalized == prefix or not normalized.startswith(f"{prefix}/"):
        return None
    return normalized.removeprefix(f"{prefix}/").split("/", 1)[0] or None
