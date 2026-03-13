from __future__ import annotations

from urllib.parse import quote, unquote


MEMORY_NAMESPACE_PREFIX = "ns:"
MEMORY_SESSION_SEPARATOR = "|sid:"


def normalize_memory_namespace(memory_namespace: str | None) -> str | None:
    value = str(memory_namespace or "").strip()
    return value or None


def build_memory_session_key(session_id: str | None, memory_namespace: str | None = None) -> str | None:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None
    normalized_namespace = normalize_memory_namespace(memory_namespace)
    if not normalized_namespace:
        return normalized_session_id
    encoded_namespace = quote(normalized_namespace, safe="")
    return f"{MEMORY_NAMESPACE_PREFIX}{encoded_namespace}{MEMORY_SESSION_SEPARATOR}{normalized_session_id}"


def parse_memory_session_key(session_key: str | None) -> tuple[str | None, str | None]:
    value = str(session_key or "").strip()
    if not value:
        return None, None
    if value.startswith(MEMORY_NAMESPACE_PREFIX) and MEMORY_SESSION_SEPARATOR in value:
        encoded_namespace, session_id = value[len(MEMORY_NAMESPACE_PREFIX) :].split(MEMORY_SESSION_SEPARATOR, 1)
        return unquote(encoded_namespace), session_id or None
    return None, value
