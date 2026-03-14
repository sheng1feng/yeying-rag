from __future__ import annotations

from pathlib import Path


FILE_TYPE_BY_SUFFIX = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".pdf": "pdf",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".csv": "csv",
    ".html": "html",
    ".htm": "html",
}


def infer_file_type(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    return FILE_TYPE_BY_SUFFIX.get(suffix, "text")
