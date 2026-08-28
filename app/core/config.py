from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)

from app.cache import CacheProviderName
from app.persistence.state_provider import AgentStateProviderName
from app.research.models import WebSearchProviderName
from app.rag.reranking import (
    DEFAULT_BGE_RERANKER_MODEL_NAME,
    RerankerProviderName,
)
from app.rag.vector_index import VectorStoreProviderName


class Settings(BaseSettings):
    """应用运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = Field(
        default="127.0.0.1",
        min_length=1,
    )
    app_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
    )
    log_level: Literal["CRITICAL", "DEBUG", "ERROR", "INFO", "WARNING"] = "INFO"
    llm_api_key: SecretStr
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
    )
    llm_max_retries: int = Field(
        default=2,
        ge=0,
    )
    llm_cache_provider: CacheProviderName = CacheProviderName.DISABLED
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_timeout_seconds: float = Field(
        default=0.25,
        gt=0,
        le=5,
    )
    llm_cache_ttl_seconds: int = Field(
        default=600,
        ge=1,
        le=86_400,
    )
    llm_cache_namespace: str = Field(
        default="enterprise-policy-agent:llm:v1",
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9:_-]+$",
    )
    llm_cache_max_request_bytes: int = Field(
        default=262_144,
        ge=1024,
        le=1_048_576,
    )
    llm_cache_max_value_bytes: int = Field(
        default=262_144,
        ge=1024,
        le=1_048_576,
    )
    llm_singleflight_enabled: bool = True
    llm_singleflight_max_keys: int = Field(
        default=128,
        ge=1,
        le=4096,
    )
    llm_provider_limit_enabled: bool = False
    llm_provider_max_concurrency: int = Field(
        default=4,
        ge=1,
        le=256,
    )
    llm_provider_max_queue: int = Field(
        default=16,
        ge=0,
        le=4096,
    )
    llm_provider_queue_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=60,
    )
    agent_safe_tool_timeout_seconds: float = Field(
        default=65.0,
        gt=0,
    )
    agent_mutation_tool_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
    )
    agent_tool_max_attempts: int = Field(
        default=3,
        ge=1,
    )
    agent_retry_min_wait_seconds: float = Field(
        default=0.1,
        ge=0,
    )
    agent_retry_max_wait_seconds: float = Field(
        default=1.0,
        ge=0,
    )
    web_search_provider: WebSearchProviderName = WebSearchProviderName.DISABLED
    tavily_api_key: SecretStr | None = None
    web_search_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
    )
    web_search_max_results: int = Field(
        default=3,
        ge=1,
        le=5,
    )
    rag_reranker_provider: RerankerProviderName = RerankerProviderName.DISABLED
    rag_reranker_model_name: str = Field(
        default=DEFAULT_BGE_RERANKER_MODEL_NAME,
        min_length=1,
    )
    rag_reranker_device: str | None = None
    rag_reranker_batch_size: int = Field(
        default=8,
        ge=1,
        le=128,
    )
    rag_reranker_candidate_k: int = Field(
        default=20,
        ge=5,
        le=100,
    )
    rag_vector_store_provider: VectorStoreProviderName = VectorStoreProviderName.MEMORY
    rag_index_pipeline_version: str = Field(
        default="policy-index-v1",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    rag_pgvector_dsn: SecretStr = SecretStr(
        "postgresql://policy_agent:local-development-only@127.0.0.1:5432/policy_agent"
    )
    rag_pgvector_collection: str = Field(
        default="enterprise-policy-bge-small-zh-v1",
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    rag_pgvector_min_pool_size: int = Field(
        default=1,
        ge=1,
        le=16,
    )
    rag_pgvector_max_pool_size: int = Field(
        default=4,
        ge=1,
        le=64,
    )
    rag_pgvector_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )
    agent_state_provider: AgentStateProviderName = AgentStateProviderName.SQLITE
    agent_postgres_dsn: SecretStr = SecretStr(
        "postgresql://policy_agent:local-development-only@127.0.0.1:5432/policy_agent"
    )
    agent_postgres_min_pool_size: int = Field(
        default=1,
        ge=1,
        le=16,
    )
    agent_postgres_max_pool_size: int = Field(
        default=8,
        ge=1,
        le=64,
    )
    agent_postgres_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
    )
    sqlite_database_path: Path = Path("data/runtime/enterprise_policy_agent.db")

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("redis_url must use redis:// or rediss:// with a host")
        return normalized

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("rag_reranker_device", mode="before")
    @classmethod
    def normalize_optional_reranker_device(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("rag_pgvector_dsn", mode="before")
    @classmethod
    def validate_pgvector_dsn(cls, value: object) -> object:
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(raw_value, str):
            return value
        normalized = raw_value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise ValueError("rag_pgvector_dsn must use postgres:// or postgresql:// with a host")
        return normalized

    @field_validator("agent_postgres_dsn", mode="before")
    @classmethod
    def validate_agent_postgres_dsn(cls, value: object) -> object:
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(raw_value, str):
            return value
        normalized = raw_value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise ValueError("agent_postgres_dsn must use postgres:// or postgresql:// with a host")
        return normalized

    @model_validator(mode="after")
    def validate_agent_retry_wait_range(self) -> Self:
        if self.agent_retry_max_wait_seconds < self.agent_retry_min_wait_seconds:
            raise ValueError(
                "agent_retry_max_wait_seconds must be greater than or equal to "
                "agent_retry_min_wait_seconds"
            )
        return self

    @model_validator(mode="after")
    def validate_web_search_configuration(self) -> Self:
        if self.web_search_provider is WebSearchProviderName.TAVILY:
            api_key = (
                self.tavily_api_key.get_secret_value().strip()
                if self.tavily_api_key is not None
                else ""
            )
            if not api_key:
                raise ValueError("tavily_api_key is required when web_search_provider is tavily")
        return self

    @model_validator(mode="after")
    def validate_pgvector_pool_range(self) -> Self:
        if self.rag_pgvector_max_pool_size < self.rag_pgvector_min_pool_size:
            raise ValueError(
                "rag_pgvector_max_pool_size must be greater than or equal to "
                "rag_pgvector_min_pool_size"
            )
        return self

    @model_validator(mode="after")
    def validate_agent_postgres_pool_range(self) -> Self:
        if self.agent_postgres_max_pool_size < self.agent_postgres_min_pool_size:
            raise ValueError(
                "agent_postgres_max_pool_size must be greater than or equal to "
                "agent_postgres_min_pool_size"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """读取并缓存应用配置。"""

    return Settings()
