from __future__ import annotations

import hashlib
import math

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from knowledge.core.settings import get_settings


class EmbeddingProvider:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


class MockEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            raw = []
            for i in range(self.dimensions):
                value = digest[i % len(digest)] / 255.0
                raw.append(value)
            norm = math.sqrt(sum(x * x for x in raw)) or 1.0
            vectors.append([x / norm for x in raw])
        return vectors


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.client = OpenAIEmbeddings(base_url=base_url, api_key=api_key or "dummy", model=model)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.client.embed_query(text)


class LangChainEmbeddingAdapter(Embeddings):
    def __init__(self, provider: EmbeddingProvider) -> None:
        self.provider = provider

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.provider.embed_texts(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.provider.embed_query(text)


def build_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.model_provider_mode == "openai_compatible" and settings.model_gateway_base_url:
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.model_gateway_base_url,
            api_key=settings.model_gateway_api_key,
            model=settings.embedding_model,
        )
    return MockEmbeddingProvider(dimensions=settings.embedding_dimensions)


def build_langchain_embeddings() -> Embeddings:
    return LangChainEmbeddingAdapter(build_embedding_provider())
