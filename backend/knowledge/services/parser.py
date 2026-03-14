from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from pypdf import PdfReader


class DocumentParser:
    TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv", ".html", ".htm", ".yaml", ".yml"}

    def parse(self, file_name: str, content: bytes) -> str:
        suffix = Path(file_name).suffix.lower()
        if suffix == ".pdf":
            return self._parse_pdf(content)
        if suffix == ".json":
            data = json.loads(content.decode("utf-8"))
            return json.dumps(data, ensure_ascii=False, indent=2)
        if suffix == ".csv":
            reader = csv.reader(io.StringIO(content.decode("utf-8")))
            return "\n".join(", ".join(row) for row in reader)
        return content.decode("utf-8", errors="ignore")

    def _parse_pdf(self, content: bytes) -> str:
        reader = PdfReader(io.BytesIO(content))
        texts = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(texts).strip()
