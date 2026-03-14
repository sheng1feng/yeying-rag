from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from langchain_core.documents import Document
from langchain_weaviate import WeaviateVectorStore as LangChainWeaviateVectorStore
from sqlalchemy import select
from sqlalchemy.orm import Session
import weaviate
from weaviate.auth import AuthApiKey
import weaviate.classes.config as wc
from weaviate.classes.query import Filter

from knowledge.core.settings import get_settings
from knowledge.services.embedding import build_langchain_embeddings
from knowledge.models import EmbeddingRecord, ImportedChunk, ImportedDocument


@dataclass(frozen=True)
class VectorSearchFilterPlan:
    wallet_address: str
    kb_ids: tuple[int, ...]
    source_paths: tuple[str, ...]
    source_kinds: tuple[str, ...]
    document_ids: tuple[int, ...]


def build_vector_search_filter_plan(
    wallet_address: str,
    kb_ids: Iterable[int],
    filters: dict | None = None,
) -> VectorSearchFilterPlan:
    normalized_filters = dict(filters or {})
    normalized_kb_ids = tuple(dict.fromkeys(int(kb_id) for kb_id in kb_ids))
    source_paths = tuple(dict.fromkeys(path for path in (normalized_filters.get("source_paths") or []) if str(path).strip()))
    source_kinds = tuple(dict.fromkeys(kind for kind in (normalized_filters.get("source_kinds") or []) if str(kind).strip()))
    document_ids = tuple(
        dict.fromkeys(int(value) for value in (normalized_filters.get("document_ids") or []) if str(value).strip())
    )
    return VectorSearchFilterPlan(
        wallet_address=wallet_address,
        kb_ids=normalized_kb_ids,
        source_paths=source_paths,
        source_kinds=source_kinds,
        document_ids=document_ids,
    )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
    return numerator / (left_norm * right_norm)


class DBVectorStore:
    backend_name = "db"

    def search(
        self,
        db: Session,
        wallet_address: str,
        kb_ids: Iterable[int],
        query_vector: list[float],
        top_k: int,
        query_text: str | None = None,
        filters: dict | None = None,
    ) -> list[dict]:
        _ = query_text
        filter_plan = build_vector_search_filter_plan(wallet_address=wallet_address, kb_ids=kb_ids, filters=filters)
        if not filter_plan.kb_ids:
            return []

        rows = db.execute(
            select(EmbeddingRecord, ImportedChunk, ImportedDocument)
            .join(ImportedChunk, ImportedChunk.id == EmbeddingRecord.chunk_id)
            .join(ImportedDocument, ImportedDocument.id == ImportedChunk.document_id)
            .where(EmbeddingRecord.owner_wallet_address == filter_plan.wallet_address)
            .where(EmbeddingRecord.kb_id.in_(filter_plan.kb_ids))
        ).all()

        results = []
        for embedding, chunk, document in rows:
            if not self._matches_filter_plan(document=document, metadata=chunk.metadata_json or {}, filter_plan=filter_plan):
                continue
            score = cosine_similarity(query_vector, embedding.vector_json)
            results.append(
                {
                    "chunk_id": chunk.id,
                    "kb_id": chunk.kb_id,
                    "document_id": chunk.document_id,
                    "source_path": document.source_path,
                    "text": chunk.text,
                    "score": score,
                    "metadata": chunk.metadata_json,
                }
            )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]

    @staticmethod
    def _matches_filter_plan(document: ImportedDocument, metadata: dict, filter_plan: VectorSearchFilterPlan) -> bool:
        if filter_plan.source_paths and document.source_path not in set(filter_plan.source_paths):
            return False
        if filter_plan.source_kinds and metadata.get("source_kind") not in set(filter_plan.source_kinds):
            return False
        if filter_plan.document_ids and document.id not in set(filter_plan.document_ids):
            return False
        return True

    def index_chunks(self, payloads: list[dict]) -> None:
        _ = payloads

    def delete_vectors(self, vector_ids: list[str]) -> None:
        _ = vector_ids

    def health(self) -> dict:
        return {"backend": "db", "status": "ok"}

    def close(self) -> None:
        return None


class WeaviateVectorStore:
    backend_name = "weaviate"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: weaviate.WeaviateClient | None = None
        self._store: LangChainWeaviateVectorStore | None = None

    def _connect(self) -> weaviate.WeaviateClient:
        if self._client is not None:
            return self._client
        auth = AuthApiKey(self.settings.weaviate_api_key) if self.settings.weaviate_api_key else None
        self._client = weaviate.connect_to_custom(
            http_host=self.settings.weaviate_host,
            http_port=self.settings.weaviate_port,
            http_secure=self.settings.weaviate_scheme == "https",
            grpc_host=self.settings.weaviate_host,
            grpc_port=self.settings.weaviate_grpc_port,
            grpc_secure=self.settings.weaviate_scheme == "https",
            auth_credentials=auth,
            skip_init_checks=False,
        )
        return self._client

    def _store_client(self) -> LangChainWeaviateVectorStore:
        if self._store is not None:
            return self._store
        client = self._connect()
        self._ensure_collection_schema(client)
        self._store = LangChainWeaviateVectorStore(
            client=client,
            index_name=self.settings.weaviate_index_name,
            text_key="text",
            embedding=build_langchain_embeddings(),
            attributes=[
                "wallet_address",
                "kb_id",
                "document_id",
                "chunk_id",
                "source_path",
                "source_kind",
                "file_name",
                "file_type",
                "chunk_index",
                "source_version",
                "chunk_strategy",
            ],
        )
        return self._store

    def _required_properties(self) -> list[wc.Property]:
        return [
            wc.Property(name="wallet_address", data_type=wc.DataType.TEXT, skip_vectorization=True),
            wc.Property(name="kb_id", data_type=wc.DataType.INT, skip_vectorization=True),
            wc.Property(name="document_id", data_type=wc.DataType.INT, skip_vectorization=True),
            wc.Property(name="chunk_id", data_type=wc.DataType.INT, skip_vectorization=True),
            wc.Property(name="source_path", data_type=wc.DataType.TEXT, skip_vectorization=True),
            wc.Property(name="source_kind", data_type=wc.DataType.TEXT, skip_vectorization=True),
            wc.Property(name="file_name", data_type=wc.DataType.TEXT, skip_vectorization=True),
            wc.Property(name="file_type", data_type=wc.DataType.TEXT, skip_vectorization=True),
            wc.Property(name="chunk_index", data_type=wc.DataType.INT, skip_vectorization=True),
            wc.Property(name="source_version", data_type=wc.DataType.TEXT, skip_vectorization=True),
            wc.Property(name="chunk_strategy", data_type=wc.DataType.TEXT, skip_vectorization=True),
        ]

    def _ensure_collection_schema(self, client: weaviate.WeaviateClient) -> None:
        index_name = self.settings.weaviate_index_name
        required = {prop.name: prop for prop in self._required_properties()}
        if not client.collections.exists(index_name):
            client.collections.create(
                name=index_name,
                properties=[wc.Property(name="text", data_type=wc.DataType.TEXT)] + list(required.values()),
                vectorizer_config=wc.Configure.Vectorizer.none(),
            )
            return

        collection = client.collections.get(index_name)
        config = collection.config.get(simple=False)
        existing = {prop.name for prop in config.properties}
        for name, prop in required.items():
            if name not in existing:
                collection.config.add_property(prop)

    def _build_filters(self, filter_plan: VectorSearchFilterPlan):
        where = Filter.by_property("wallet_address").equal(filter_plan.wallet_address)
        if len(filter_plan.kb_ids) == 1:
            where = where & Filter.by_property("kb_id").equal(filter_plan.kb_ids[0])
        elif len(filter_plan.kb_ids) > 1:
            kb_filter = self._or_equal_filter("kb_id", list(filter_plan.kb_ids))
            if kb_filter is not None:
                where = where & kb_filter
        if filter_plan.source_paths:
            source_path_filter = self._or_equal_filter("source_path", list(filter_plan.source_paths))
            if source_path_filter is not None:
                where = where & source_path_filter
        if filter_plan.source_kinds:
            source_kind_filter = self._or_equal_filter("source_kind", list(filter_plan.source_kinds))
            if source_kind_filter is not None:
                where = where & source_kind_filter
        if filter_plan.document_ids:
            document_filter = self._or_equal_filter("document_id", list(filter_plan.document_ids))
            if document_filter is not None:
                where = where & document_filter
        return where

    @staticmethod
    def _or_equal_filter(property_name: str, values: list):
        clauses = [Filter.by_property(property_name).equal(value) for value in values]
        if not clauses:
            return None
        current = clauses[0]
        for clause in clauses[1:]:
            current = current | clause
        return current

    def index_chunks(self, payloads: list[dict]) -> None:
        if not payloads:
            return
        documents = []
        ids = []
        for item in payloads:
            documents.append(
                Document(
                    id=item["vector_id"],
                    page_content=item["text"],
                    metadata=item["metadata"],
                )
            )
            ids.append(item["vector_id"])
        self._store_client().add_documents(documents, ids=ids)

    def delete_vectors(self, vector_ids: list[str]) -> None:
        if not vector_ids:
            return
        client = self._connect()
        collection = client.collections.get(self.settings.weaviate_index_name)
        for vector_id in vector_ids:
            try:
                collection.data.delete_by_id(uuid.UUID(str(vector_id)))
            except Exception:
                continue

    def search(
        self,
        db: Session,
        wallet_address: str,
        kb_ids: Iterable[int],
        query_vector: list[float],
        top_k: int,
        query_text: str | None = None,
        filters: dict | None = None,
    ) -> list[dict]:
        _ = db
        store = self._store_client()
        filter_plan = build_vector_search_filter_plan(wallet_address=wallet_address, kb_ids=kb_ids, filters=filters)
        filters = self._build_filters(filter_plan)
        docs_and_scores = store.similarity_search_with_score(
            query=query_text or "",
            k=top_k,
            filters=filters,
            vector=query_vector,
            return_properties=[
                "wallet_address",
                "kb_id",
                "document_id",
                "chunk_id",
                "source_path",
                "source_kind",
                "file_name",
                "file_type",
                "chunk_index",
                "source_version",
                "chunk_strategy",
            ],
        )
        results = []
        for doc, score in docs_and_scores:
            metadata = doc.metadata or {}
            results.append(
                {
                    "chunk_id": int(metadata.get("chunk_id") or 0),
                    "kb_id": int(metadata.get("kb_id") or 0),
                    "document_id": int(metadata.get("document_id") or 0),
                    "source_path": metadata.get("source_path") or "",
                    "text": doc.page_content,
                    "score": float(score),
                    "metadata": metadata,
                }
            )
        return results

    def health(self) -> dict:
        client = self._connect()
        return {
            "backend": "weaviate",
            "live": client.is_live(),
            "ready": client.is_ready(),
            "url": self.settings.weaviate_url,
            "index_name": self.settings.weaviate_index_name,
        }

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None
                self._store = None


@lru_cache(maxsize=1)
def build_vector_store():
    settings = get_settings()
    if settings.vector_store_mode == "weaviate":
        return WeaviateVectorStore()
    return DBVectorStore()


def close_vector_store() -> None:
    store = build_vector_store()
    close = getattr(store, "close", None)
    if callable(close):
        close()
    build_vector_store.cache_clear()
