from __future__ import annotations

import math
import uuid
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


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
    return numerator / (left_norm * right_norm)


class DBVectorStore:
    def search(
        self,
        db: Session,
        wallet_address: str,
        kb_ids: Iterable[int],
        query_vector: list[float],
        top_k: int,
        query_text: str | None = None,
    ) -> list[dict]:
        _ = query_text
        kb_ids = list(kb_ids)
        if not kb_ids:
            return []

        rows = db.execute(
            select(EmbeddingRecord, ImportedChunk, ImportedDocument)
            .join(ImportedChunk, ImportedChunk.id == EmbeddingRecord.chunk_id)
            .join(ImportedDocument, ImportedDocument.id == ImportedChunk.document_id)
            .where(EmbeddingRecord.owner_wallet_address == wallet_address)
            .where(EmbeddingRecord.kb_id.in_(kb_ids))
        ).all()

        results = []
        for embedding, chunk, document in rows:
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

    def index_chunks(self, payloads: list[dict]) -> None:
        _ = payloads

    def delete_vectors(self, vector_ids: list[str]) -> None:
        _ = vector_ids

    def health(self) -> dict:
        return {"backend": "db", "status": "ok"}

    def close(self) -> None:
        return None


class WeaviateVectorStore:
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

    def _build_filters(self, wallet_address: str, kb_ids: Iterable[int]):
        kb_ids = list(kb_ids)
        where = Filter.by_property("wallet_address").equal(wallet_address)
        if len(kb_ids) == 1:
            return where & Filter.by_property("kb_id").equal(kb_ids[0])
        if len(kb_ids) > 1:
            kb_filter = None
            for kb_id in kb_ids:
                clause = Filter.by_property("kb_id").equal(kb_id)
                kb_filter = clause if kb_filter is None else (kb_filter | clause)
            return where & kb_filter
        return where

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
    ) -> list[dict]:
        _ = db
        store = self._store_client()
        filters = self._build_filters(wallet_address, kb_ids)
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
