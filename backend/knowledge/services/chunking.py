from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from knowledge.services.filetypes import infer_file_type


@dataclass
class ChunkResult:
    text: str
    metadata: dict


class DocumentChunker:
    def chunk(self, file_name: str, parsed_text: str, config: dict) -> list[ChunkResult]:
        file_type = infer_file_type(file_name)
        if file_type == "markdown":
            return self._chunk_markdown(parsed_text, config)
        if file_type == "pdf":
            return self._chunk_pdf(parsed_text, config)
        if file_type == "json":
            return self._chunk_json(parsed_text, config)
        if file_type == "csv":
            return self._chunk_csv(parsed_text, config)
        if file_type == "yaml":
            return self._chunk_yaml(parsed_text, config)
        return self._chunk_text(parsed_text, config, file_type=file_type)

    def _text_splitter(self, chunk_size: int, chunk_overlap: int, separators: list[str]) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
        )

    def _chunk_text(self, text: str, config: dict, file_type: str = "text") -> list[ChunkResult]:
        splitter = self._text_splitter(
            chunk_size=int(config.get("chunk_size", 800)),
            chunk_overlap=int(config.get("chunk_overlap", 120)),
            separators=["\n\n", "\n", "。", "！", "？", ".", " ", ""],
        )
        parts = splitter.split_text(text)
        return [
            ChunkResult(
                text=part,
                metadata={"chunk_strategy": f"{file_type}_recursive", "char_count": len(part)},
            )
            for part in parts
            if part.strip()
        ]

    def _chunk_pdf(self, text: str, config: dict) -> list[ChunkResult]:
        splitter = self._text_splitter(
            chunk_size=max(500, int(config.get("chunk_size", 800)) - 120),
            chunk_overlap=max(60, int(config.get("chunk_overlap", 120)) - 40),
            separators=["\n\n", "\n", "。", "！", "？", ".", " ", ""],
        )
        parts = splitter.split_text(text)
        return [
            ChunkResult(
                text=part,
                metadata={"chunk_strategy": "pdf_recursive_dense", "char_count": len(part)},
            )
            for part in parts
            if part.strip()
        ]

    def _chunk_markdown(self, text: str, config: dict) -> list[ChunkResult]:
        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
            strip_headers=False,
        )
        sections = header_splitter.split_text(text)
        recursive = self._text_splitter(
            chunk_size=int(config.get("chunk_size", 800)),
            chunk_overlap=int(config.get("chunk_overlap", 120)),
            separators=["\n## ", "\n### ", "\n\n", "\n", "。", ".", " ", ""],
        )
        out: list[ChunkResult] = []
        if not sections:
            return self._chunk_text(text, config, file_type="markdown")
        for section in sections:
            parts = recursive.split_text(section.page_content)
            section_meta = dict(section.metadata)
            for part in parts:
                if not part.strip():
                    continue
                out.append(
                    ChunkResult(
                        text=part,
                        metadata={
                            "chunk_strategy": "markdown_header_recursive",
                            "section": section_meta,
                            "char_count": len(part),
                        },
                    )
                )
        return out

    def _chunk_json(self, text: str, config: dict) -> list[ChunkResult]:
        try:
            data = json.loads(text)
        except Exception:
            return self._chunk_text(text, config, file_type="json")
        entries = self._flatten_json(data)
        if not entries:
            return self._chunk_text(text, config, file_type="json")
        chunk_size = max(5, int(config.get("chunk_size", 800)) // 80)
        groups = [entries[index : index + chunk_size] for index in range(0, len(entries), chunk_size)]
        return [
            ChunkResult(
                text="\n".join(group),
                metadata={
                    "chunk_strategy": "json_flattened_group",
                    "entry_count": len(group),
                    "char_count": sum(len(item) for item in group),
                },
            )
            for group in groups
        ]

    def _flatten_json(self, value, prefix: str = "$") -> list[str]:
        if isinstance(value, dict):
            items: list[str] = []
            for key, item in value.items():
                items.extend(self._flatten_json(item, f"{prefix}.{key}"))
            return items
        if isinstance(value, list):
            items: list[str] = []
            for index, item in enumerate(value):
                items.extend(self._flatten_json(item, f"{prefix}[{index}]"))
            return items
        return [f"{prefix} = {value}"]

    def _chunk_csv(self, text: str, config: dict) -> list[ChunkResult]:
        reader = list(csv.reader(io.StringIO(text)))
        if not reader:
            return []
        header = reader[0]
        rows = reader[1:]
        rows_per_chunk = max(10, int(config.get("chunk_size", 800)) // 60)
        output: list[ChunkResult] = []
        for offset in range(0, len(rows), rows_per_chunk):
            group = rows[offset : offset + rows_per_chunk]
            lines = [", ".join(header)]
            lines.extend(", ".join(row) for row in group)
            text_block = "\n".join(lines)
            output.append(
                ChunkResult(
                    text=text_block,
                    metadata={
                        "chunk_strategy": "csv_row_group",
                        "rows": len(group),
                        "char_count": len(text_block),
                    },
                )
            )
        return output

    def _chunk_yaml(self, text: str, config: dict) -> list[ChunkResult]:
        splitter = self._text_splitter(
            chunk_size=int(config.get("chunk_size", 800)),
            chunk_overlap=int(config.get("chunk_overlap", 120)),
            separators=["\n\n", "\n- ", "\n", ": ", " ", ""],
        )
        parts = splitter.split_text(text)
        return [
            ChunkResult(
                text=part,
                metadata={"chunk_strategy": "yaml_recursive", "char_count": len(part)},
            )
            for part in parts
            if part.strip()
        ]
