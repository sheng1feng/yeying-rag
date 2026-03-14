from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import base64
import hashlib
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    app_name: str = "knowledge"
    app_env: str = "development"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str = "sqlite:///./knowledge.db"
    sqlite_busy_timeout_ms: int = 15000

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_minutes: int = 60 * 24 * 7
    challenge_ttl_seconds: int = 300

    warehouse_gateway_mode: str = "mock"
    warehouse_base_url: str = "https://webdav.yeying.pub"
    warehouse_webdav_prefix: str = "/dav"
    warehouse_auth_mode: str = "jwt"
    warehouse_service_bearer: str = ""
    warehouse_forward_wallet_header: str = "X-End-User-Wallet"
    warehouse_mock_root: str = str(Path(__file__).resolve().parents[3] / ".mock_warehouse")
    token_encryption_secret: str = ""
    warehouse_ucan_audience: str = "did:web:webdav.yeying.pub"
    warehouse_ucan_resource: str = "profile"
    warehouse_ucan_action: str = "read"
    warehouse_ucan_chain_id: int = 1
    warehouse_ucan_siwe_domain: str = "knowledge.yeying.pub"
    warehouse_ucan_siwe_uri: str = "https://knowledge.yeying.pub"
    warehouse_ucan_bootstrap_ttl_seconds: int = 300
    warehouse_ucan_root_ttl_hours: int = 168
    warehouse_ucan_invocation_ttl_minutes: int = 15

    vector_store_mode: str = "db"
    weaviate_url: str = "http://127.0.0.1:8080"
    weaviate_index_name: str = "KnowledgeChunk"
    weaviate_enabled: bool = False
    weaviate_scheme: str = "http"
    weaviate_host: str = ""
    weaviate_port: int = 8080
    weaviate_grpc_port: int = 50051
    weaviate_api_key: str = ""

    model_provider_mode: str = "mock"
    model_gateway_base_url: str = ""
    model_gateway_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 32
    rerank_enabled: bool = False
    rerank_model: str = ""
    rerank_api_base: str = ""
    rerank_api_key: str = ""

    chunk_size: int = 800
    chunk_overlap: int = 120
    retrieval_top_k: int = 6
    memory_top_k: int = 4
    auto_memory_short_term_ttl_hours: int = 72
    auto_memory_max_long_terms: int = 3
    auto_memory_max_short_terms: int = 4
    auto_memory_recent_turn_max_chars: int = 600
    worker_poll_interval_seconds: int = 5
    worker_run_lease_ttl_seconds: int = 120
    worker_task_concurrency: int = 2
    worker_max_active_tasks_per_user: int = 1
    worker_task_heartbeat_interval_seconds: int = 15
    worker_name: str = "knowledge-worker-1"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.token_encryption_secret:
        digest = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
        settings.token_encryption_secret = base64.urlsafe_b64encode(digest).decode("utf-8")
    if (not settings.weaviate_url or settings.weaviate_url == "http://127.0.0.1:8080") and settings.weaviate_host:
        settings.weaviate_url = f"{settings.weaviate_scheme}://{settings.weaviate_host}:{settings.weaviate_port}"
    parsed = urlparse(settings.weaviate_url)
    if parsed.scheme and parsed.hostname:
        settings.weaviate_scheme = parsed.scheme
        settings.weaviate_host = parsed.hostname
        if parsed.port:
            settings.weaviate_port = parsed.port
    return settings
