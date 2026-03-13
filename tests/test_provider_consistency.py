from __future__ import annotations

from types import SimpleNamespace

from knowledge.services import embedding as embedding_module
from knowledge.services import vector_store as vector_store_module


def test_vector_search_filter_plan_normalizes_shared_filters():
    plan = vector_store_module.build_vector_search_filter_plan(
        wallet_address="wallet-1",
        kb_ids=[3, 3, 5],
        filters={
            "source_paths": ["/a", "/a", " ", "/b"],
            "source_kinds": ["personal", "personal", "", "shared"],
            "document_ids": ["9", 9, "10", " "],
        },
    )
    assert plan.wallet_address == "wallet-1"
    assert plan.kb_ids == (3, 5)
    assert plan.source_paths == ("/a", "/b")
    assert plan.source_kinds == ("personal", "shared")
    assert plan.document_ids == (9, 10)


def test_db_filter_matching_uses_shared_filter_plan():
    plan = vector_store_module.build_vector_search_filter_plan(
        wallet_address="wallet-1",
        kb_ids=[1],
        filters={"source_paths": ["/a"], "source_kinds": ["personal"], "document_ids": [5]},
    )
    document = SimpleNamespace(id=5, source_path="/a")
    assert vector_store_module.DBVectorStore._matches_filter_plan(document, {"source_kind": "personal"}, plan) is True
    assert vector_store_module.DBVectorStore._matches_filter_plan(document, {"source_kind": "shared"}, plan) is False
    assert vector_store_module.DBVectorStore._matches_filter_plan(SimpleNamespace(id=7, source_path="/a"), {"source_kind": "personal"}, plan) is False


def test_weaviate_filter_builder_uses_shared_plan(monkeypatch):
    class FakeExpression:
        def __init__(self, op: str, payload):
            self.op = op
            self.payload = payload

        def __and__(self, other: "FakeExpression") -> "FakeExpression":
            return FakeExpression("and", [self, other])

        def __or__(self, other: "FakeExpression") -> "FakeExpression":
            return FakeExpression("or", [self, other])

    class FakePropertyBuilder:
        def __init__(self, property_name: str) -> None:
            self.property_name = property_name

        def equal(self, value):
            return FakeExpression("eq", (self.property_name, value))

    class FakeFilter:
        @staticmethod
        def by_property(property_name: str) -> FakePropertyBuilder:
            return FakePropertyBuilder(property_name)

    def flatten(expression: FakeExpression) -> list[tuple[str, object]]:
        if expression.op == "eq":
            return [expression.payload]
        values: list[tuple[str, object]] = []
        for child in expression.payload:
            values.extend(flatten(child))
        return values

    monkeypatch.setattr(vector_store_module, "Filter", FakeFilter)
    monkeypatch.setattr(
        vector_store_module,
        "get_settings",
        lambda: SimpleNamespace(
            weaviate_api_key="",
            weaviate_host="localhost",
            weaviate_port=8080,
            weaviate_scheme="http",
            weaviate_grpc_port=50051,
            weaviate_index_name="KnowledgeChunk",
            weaviate_url="http://localhost:8080",
        ),
    )
    store = vector_store_module.WeaviateVectorStore()
    plan = vector_store_module.build_vector_search_filter_plan(
        wallet_address="wallet-7",
        kb_ids=[1, 2],
        filters={"source_paths": ["/a"], "source_kinds": ["personal"], "document_ids": [9]},
    )
    expression = store._build_filters(plan)
    flattened = flatten(expression)
    assert ("wallet_address", "wallet-7") in flattened
    assert ("kb_id", 1) in flattened
    assert ("kb_id", 2) in flattened
    assert ("source_path", "/a") in flattened
    assert ("source_kind", "personal") in flattened
    assert ("document_id", 9) in flattened


def test_openai_compatible_embedding_provider_uses_openai_client(monkeypatch):
    calls: dict = {}

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs) -> None:
            calls["init"] = kwargs

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            calls["documents"] = texts
            return [[0.1, 0.2] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            calls["query"] = text
            return [0.3, 0.4]

    monkeypatch.setattr(embedding_module, "OpenAIEmbeddings", FakeOpenAIEmbeddings)
    provider = embedding_module.OpenAICompatibleEmbeddingProvider(
        base_url="https://gateway.example.com",
        api_key="",
        model="text-embedding-3-small",
    )
    assert provider.embed_texts(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
    assert provider.embed_query("hello") == [0.3, 0.4]
    assert calls["init"]["base_url"] == "https://gateway.example.com"
    assert calls["init"]["api_key"] == "dummy"
    assert calls["documents"] == ["a", "b"]
    assert calls["query"] == "hello"


def test_build_embedding_provider_selects_provider_mode(monkeypatch):
    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [0.0, 1.0]

    monkeypatch.setattr(embedding_module, "OpenAIEmbeddings", FakeOpenAIEmbeddings)
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: SimpleNamespace(
            model_provider_mode="openai_compatible",
            model_gateway_base_url="https://gateway.example.com",
            model_gateway_api_key="secret",
            embedding_model="text-embedding-3-small",
            embedding_dimensions=16,
        ),
    )
    provider = embedding_module.build_embedding_provider()
    assert isinstance(provider, embedding_module.OpenAICompatibleEmbeddingProvider)

    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: SimpleNamespace(
            model_provider_mode="mock",
            model_gateway_base_url="",
            model_gateway_api_key="",
            embedding_model="text-embedding-3-small",
            embedding_dimensions=12,
        ),
    )
    provider = embedding_module.build_embedding_provider()
    assert isinstance(provider, embedding_module.MockEmbeddingProvider)
    assert len(provider.embed_query("mock")) == 12
