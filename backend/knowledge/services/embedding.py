from __future__ import annotations

import hashlib
import math

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from knowledge.core.settings import get_settings


class EmbeddingProvider:
    provider_name = "unknown"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def diagnostics(self) -> dict:
        return {"provider_name": self.provider_name}


class MockEmbeddingProvider(EmbeddingProvider):
    provider_name = "mock"

    def __init__(self, dimensions: int, configured_mode: str = "mock", fallback_reason: str = "") -> None:
        self.dimensions = dimensions
        self.configured_mode = configured_mode
        self.fallback_reason = fallback_reason

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

    def diagnostics(self) -> dict:
        return {
            "provider_name": self.provider_name,
            "configured_mode": self.configured_mode,
            "dimensions": self.dimensions,
            "fallback_reason": self.fallback_reason,
        }


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    provider_name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url
        self.model = model
        self.client = OpenAIEmbeddings(base_url=base_url, api_key=api_key or "dummy", model=model)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.client.embed_query(text)

    def diagnostics(self) -> dict:
        return {
            "provider_name": self.provider_name,
            "configured_mode": self.provider_name,
            "base_url": self.base_url,
            "model": self.model,
            "fallback_reason": "",
        }


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
    fallback_reason = ""
    if settings.model_provider_mode == "openai_compatible" and not settings.model_gateway_base_url:
        fallback_reason = "model_gateway_base_url missing; using mock embedding provider"
    return MockEmbeddingProvider(
        dimensions=settings.embedding_dimensions,
        configured_mode=settings.model_provider_mode,
        fallback_reason=fallback_reason,
    )


def build_langchain_embeddings() -> Embeddings:
    return LangChainEmbeddingAdapter(build_embedding_provider())
