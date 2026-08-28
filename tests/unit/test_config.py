from pathlib import Path

import pytest
from pydantic import ValidationError

from app.cache import CacheProviderName
from app.core.config import Settings
from app.persistence import AgentStateProviderName
from app.rag.reranking import RerankerProviderName
from app.rag.vector_index import VectorStoreProviderName
from app.research import WebSearchProviderName

_ENVIRONMENT_NAMES = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
    "LLM_CACHE_PROVIDER",
    "REDIS_URL",
    "REDIS_TIMEOUT_SECONDS",
    "LLM_CACHE_TTL_SECONDS",
    "LLM_CACHE_NAMESPACE",
    "LLM_CACHE_MAX_REQUEST_BYTES",
    "LLM_CACHE_MAX_VALUE_BYTES",
    "LLM_SINGLEFLIGHT_ENABLED",
    "LLM_SINGLEFLIGHT_MAX_KEYS",
    "LLM_PROVIDER_LIMIT_ENABLED",
    "LLM_PROVIDER_MAX_CONCURRENCY",
    "LLM_PROVIDER_MAX_QUEUE",
    "LLM_PROVIDER_QUEUE_TIMEOUT_SECONDS",
    "AGENT_SAFE_TOOL_TIMEOUT_SECONDS",
    "AGENT_MUTATION_TOOL_TIMEOUT_SECONDS",
    "AGENT_TOOL_MAX_ATTEMPTS",
    "AGENT_RETRY_MIN_WAIT_SECONDS",
    "AGENT_RETRY_MAX_WAIT_SECONDS",
    "WEB_SEARCH_PROVIDER",
    "TAVILY_API_KEY",
    "WEB_SEARCH_TIMEOUT_SECONDS",
    "WEB_SEARCH_MAX_RESULTS",
    "RAG_RERANKER_PROVIDER",
    "RAG_RERANKER_MODEL_NAME",
    "RAG_RERANKER_DEVICE",
    "RAG_RERANKER_BATCH_SIZE",
    "RAG_RERANKER_CANDIDATE_K",
    "RAG_VECTOR_STORE_PROVIDER",
    "RAG_PGVECTOR_DSN",
    "RAG_PGVECTOR_COLLECTION",
    "RAG_PGVECTOR_MIN_POOL_SIZE",
    "RAG_PGVECTOR_MAX_POOL_SIZE",
    "RAG_PGVECTOR_CONNECT_TIMEOUT_SECONDS",
    "AGENT_STATE_PROVIDER",
    "AGENT_POSTGRES_DSN",
    "AGENT_POSTGRES_MIN_POOL_SIZE",
    "AGENT_POSTGRES_MAX_POOL_SIZE",
    "AGENT_POSTGRES_CONNECT_TIMEOUT_SECONDS",
    "SQLITE_DATABASE_PATH",
)


@pytest.fixture(autouse=True)
def clear_llm_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_uses_default_llm_settings() -> None:
    settings = Settings(
        llm_api_key="test-key",
        _env_file=None,
    )

    assert settings.llm_base_url == ("https://api.deepseek.com")
    assert settings.llm_model == ("deepseek-v4-flash")
    assert settings.llm_timeout_seconds == 60.0
    assert settings.llm_max_retries == 2
    assert settings.llm_cache_provider is CacheProviderName.DISABLED
    assert settings.redis_url == "redis://127.0.0.1:6379/0"
    assert settings.redis_timeout_seconds == 0.25
    assert settings.llm_cache_ttl_seconds == 600
    assert settings.llm_cache_namespace == "enterprise-policy-agent:llm:v1"
    assert settings.llm_cache_max_request_bytes == 262_144
    assert settings.llm_cache_max_value_bytes == 262_144
    assert settings.llm_singleflight_enabled is True
    assert settings.llm_singleflight_max_keys == 128
    assert settings.llm_provider_limit_enabled is False
    assert settings.llm_provider_max_concurrency == 4
    assert settings.llm_provider_max_queue == 16
    assert settings.llm_provider_queue_timeout_seconds == 2.0
    assert settings.agent_safe_tool_timeout_seconds == 65.0
    assert settings.agent_mutation_tool_timeout_seconds == 10.0
    assert settings.agent_tool_max_attempts == 3
    assert settings.agent_retry_min_wait_seconds == 0.1
    assert settings.agent_retry_max_wait_seconds == 1.0
    assert settings.web_search_provider is WebSearchProviderName.DISABLED
    assert settings.tavily_api_key is None
    assert settings.web_search_timeout_seconds == 10.0
    assert settings.web_search_max_results == 3
    assert settings.rag_reranker_provider is RerankerProviderName.DISABLED
    assert settings.rag_reranker_model_name == "BAAI/bge-reranker-v2-m3"
    assert settings.rag_reranker_device is None
    assert settings.rag_reranker_batch_size == 8
    assert settings.rag_reranker_candidate_k == 20
    assert settings.rag_vector_store_provider is VectorStoreProviderName.MEMORY
    assert settings.rag_index_pipeline_version == "policy-index-v1"
    assert settings.rag_pgvector_dsn.get_secret_value().startswith("postgresql://")
    assert settings.rag_pgvector_collection == "enterprise-policy-bge-small-zh-v1"
    assert settings.rag_pgvector_min_pool_size == 1
    assert settings.rag_pgvector_max_pool_size == 4
    assert settings.rag_pgvector_connect_timeout_seconds == 5.0
    assert settings.agent_state_provider is AgentStateProviderName.SQLITE
    assert settings.agent_postgres_dsn.get_secret_value().startswith("postgresql://")
    assert settings.agent_postgres_min_pool_size == 1
    assert settings.agent_postgres_max_pool_size == 8
    assert settings.agent_postgres_connect_timeout_seconds == 5.0
    assert settings.sqlite_database_path == Path("data/runtime/enterprise_policy_agent.db")
    assert settings.llm_api_key.get_secret_value() == "test-key"


def test_loads_llm_settings_from_env_file(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        (
            "LLM_API_KEY=env-test-key\n"
            "LLM_BASE_URL=https://example.com/v1\n"
            "LLM_MODEL=test-model\n"
            "LLM_TIMEOUT_SECONDS=15\n"
            "LLM_MAX_RETRIES=1\n"
            "LLM_CACHE_PROVIDER=redis\n"
            "REDIS_URL=rediss://cache.example.com:6380/2\n"
            "REDIS_TIMEOUT_SECONDS=0.5\n"
            "LLM_CACHE_TTL_SECONDS=1200\n"
            "LLM_CACHE_NAMESPACE=company:agent:llm:v2\n"
            "LLM_CACHE_MAX_REQUEST_BYTES=65536\n"
            "LLM_CACHE_MAX_VALUE_BYTES=131072\n"
            "LLM_SINGLEFLIGHT_ENABLED=false\n"
            "LLM_SINGLEFLIGHT_MAX_KEYS=32\n"
            "LLM_PROVIDER_LIMIT_ENABLED=true\n"
            "LLM_PROVIDER_MAX_CONCURRENCY=6\n"
            "LLM_PROVIDER_MAX_QUEUE=24\n"
            "LLM_PROVIDER_QUEUE_TIMEOUT_SECONDS=1.5\n"
            "AGENT_SAFE_TOOL_TIMEOUT_SECONDS=20\n"
            "AGENT_MUTATION_TOOL_TIMEOUT_SECONDS=5\n"
            "AGENT_TOOL_MAX_ATTEMPTS=4\n"
            "AGENT_RETRY_MIN_WAIT_SECONDS=0.2\n"
            "AGENT_RETRY_MAX_WAIT_SECONDS=2\n"
            "WEB_SEARCH_PROVIDER=tavily\n"
            "TAVILY_API_KEY=tvly-env-test-key\n"
            "WEB_SEARCH_TIMEOUT_SECONDS=8\n"
            "WEB_SEARCH_MAX_RESULTS=4\n"
            "RAG_RERANKER_PROVIDER=bge\n"
            "RAG_RERANKER_MODEL_NAME=company/reranker\n"
            "RAG_RERANKER_DEVICE=cpu\n"
            "RAG_RERANKER_BATCH_SIZE=16\n"
            "RAG_RERANKER_CANDIDATE_K=30\n"
            "RAG_VECTOR_STORE_PROVIDER=pgvector\n"
            "RAG_INDEX_PIPELINE_VERSION=company-policy-index-v2\n"
            "RAG_PGVECTOR_DSN=postgresql://rag:secret@postgres.example:5432/rag\n"
            "RAG_PGVECTOR_COLLECTION=company-policy-v2\n"
            "RAG_PGVECTOR_MIN_POOL_SIZE=2\n"
            "RAG_PGVECTOR_MAX_POOL_SIZE=8\n"
            "RAG_PGVECTOR_CONNECT_TIMEOUT_SECONDS=9\n"
            "AGENT_STATE_PROVIDER=postgresql\n"
            "AGENT_POSTGRES_DSN=postgresql://agent:secret@postgres.example:5432/agent\n"
            "AGENT_POSTGRES_MIN_POOL_SIZE=3\n"
            "AGENT_POSTGRES_MAX_POOL_SIZE=12\n"
            "AGENT_POSTGRES_CONNECT_TIMEOUT_SECONDS=11\n"
            "SQLITE_DATABASE_PATH=data/test-agent.db"
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_path)

    assert settings.llm_api_key.get_secret_value() == "env-test-key"
    assert settings.llm_base_url == ("https://example.com/v1")
    assert settings.llm_model == "test-model"
    assert settings.llm_timeout_seconds == 15.0
    assert settings.llm_max_retries == 1
    assert settings.llm_cache_provider is CacheProviderName.REDIS
    assert settings.redis_url == "rediss://cache.example.com:6380/2"
    assert settings.redis_timeout_seconds == 0.5
    assert settings.llm_cache_ttl_seconds == 1200
    assert settings.llm_cache_namespace == "company:agent:llm:v2"
    assert settings.llm_cache_max_request_bytes == 65_536
    assert settings.llm_cache_max_value_bytes == 131_072
    assert settings.llm_singleflight_enabled is False
    assert settings.llm_singleflight_max_keys == 32
    assert settings.llm_provider_limit_enabled is True
    assert settings.llm_provider_max_concurrency == 6
    assert settings.llm_provider_max_queue == 24
    assert settings.llm_provider_queue_timeout_seconds == 1.5
    assert settings.agent_safe_tool_timeout_seconds == 20.0
    assert settings.agent_mutation_tool_timeout_seconds == 5.0
    assert settings.agent_tool_max_attempts == 4
    assert settings.agent_retry_min_wait_seconds == 0.2
    assert settings.agent_retry_max_wait_seconds == 2.0
    assert settings.web_search_provider is WebSearchProviderName.TAVILY
    assert settings.tavily_api_key is not None
    assert settings.tavily_api_key.get_secret_value() == "tvly-env-test-key"
    assert settings.web_search_timeout_seconds == 8.0
    assert settings.web_search_max_results == 4
    assert settings.rag_reranker_provider is RerankerProviderName.BGE
    assert settings.rag_reranker_model_name == "company/reranker"
    assert settings.rag_reranker_device == "cpu"
    assert settings.rag_reranker_batch_size == 16
    assert settings.rag_reranker_candidate_k == 30
    assert settings.rag_vector_store_provider is VectorStoreProviderName.PGVECTOR
    assert settings.rag_index_pipeline_version == "company-policy-index-v2"
    assert settings.rag_pgvector_dsn.get_secret_value() == (
        "postgresql://rag:secret@postgres.example:5432/rag"
    )
    assert settings.rag_pgvector_collection == "company-policy-v2"
    assert settings.rag_pgvector_min_pool_size == 2
    assert settings.rag_pgvector_max_pool_size == 8
    assert settings.rag_pgvector_connect_timeout_seconds == 9.0
    assert settings.agent_state_provider is AgentStateProviderName.POSTGRESQL
    assert settings.agent_postgres_dsn.get_secret_value() == (
        "postgresql://agent:secret@postgres.example:5432/agent"
    )
    assert settings.agent_postgres_min_pool_size == 3
    assert settings.agent_postgres_max_pool_size == 12
    assert settings.agent_postgres_connect_timeout_seconds == 11.0
    assert "postgresql://rag:secret" not in repr(settings)
    assert "postgresql://agent:secret" not in repr(settings)
    assert settings.sqlite_database_path == Path("data/test-agent.db")


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("llm_timeout_seconds", 0),
        ("llm_max_retries", -1),
        ("redis_timeout_seconds", 0),
        ("redis_timeout_seconds", 6),
        ("llm_cache_ttl_seconds", 0),
        ("llm_cache_ttl_seconds", 86_401),
        ("llm_cache_max_request_bytes", 1023),
        ("llm_cache_max_value_bytes", 1023),
        ("llm_singleflight_max_keys", 0),
        ("llm_singleflight_max_keys", 4097),
        ("llm_provider_max_concurrency", 0),
        ("llm_provider_max_concurrency", 257),
        ("llm_provider_max_queue", -1),
        ("llm_provider_max_queue", 4097),
        ("llm_provider_queue_timeout_seconds", 0),
        ("llm_provider_queue_timeout_seconds", 61),
        ("agent_safe_tool_timeout_seconds", 0),
        ("agent_mutation_tool_timeout_seconds", 0),
        ("agent_tool_max_attempts", 0),
        ("agent_retry_min_wait_seconds", -1),
        ("agent_retry_max_wait_seconds", -1),
        ("web_search_timeout_seconds", 0),
        ("web_search_max_results", 0),
        ("web_search_max_results", 6),
        ("rag_reranker_batch_size", 0),
        ("rag_reranker_batch_size", 129),
        ("rag_reranker_candidate_k", 4),
        ("rag_reranker_candidate_k", 101),
        ("rag_pgvector_min_pool_size", 0),
        ("rag_pgvector_min_pool_size", 17),
        ("rag_pgvector_max_pool_size", 0),
        ("rag_pgvector_max_pool_size", 65),
        ("rag_pgvector_connect_timeout_seconds", 0),
        ("rag_pgvector_connect_timeout_seconds", 61),
        ("agent_postgres_min_pool_size", 0),
        ("agent_postgres_min_pool_size", 17),
        ("agent_postgres_max_pool_size", 0),
        ("agent_postgres_max_pool_size", 65),
        ("agent_postgres_connect_timeout_seconds", 0),
        ("agent_postgres_connect_timeout_seconds", 61),
    ],
)
def test_rejects_invalid_numeric_settings(
    field_name: str,
    invalid_value: int,
) -> None:
    values = {
        "llm_api_key": "test-key",
        field_name: invalid_value,
    }

    with pytest.raises(ValidationError):
        Settings(
            **values,
            _env_file=None,
        )


def test_rejects_inverted_agent_retry_wait_range() -> None:
    with pytest.raises(ValidationError):
        Settings(
            llm_api_key="test-key",
            agent_retry_min_wait_seconds=2.0,
            agent_retry_max_wait_seconds=1.0,
            _env_file=None,
        )


def test_rejects_inverted_pgvector_pool_range() -> None:
    with pytest.raises(ValidationError, match="rag_pgvector_max_pool_size"):
        Settings(
            llm_api_key="test-key",
            rag_pgvector_min_pool_size=5,
            rag_pgvector_max_pool_size=4,
            _env_file=None,
        )


def test_rejects_inverted_agent_postgres_pool_range() -> None:
    with pytest.raises(ValidationError, match="agent_postgres_max_pool_size"):
        Settings(
            llm_api_key="test-key",
            agent_postgres_min_pool_size=9,
            agent_postgres_max_pool_size=8,
            _env_file=None,
        )


@pytest.mark.parametrize(
    "dsn",
    ["", "sqlite:///tmp/rag.db", "postgresql:///missing-host"],
)
def test_rejects_invalid_pgvector_dsn(dsn: str) -> None:
    with pytest.raises(ValidationError, match="rag_pgvector_dsn"):
        Settings(
            llm_api_key="test-key",
            rag_pgvector_dsn=dsn,
            _env_file=None,
        )


@pytest.mark.parametrize(
    "dsn",
    ["", "sqlite:///tmp/agent.db", "postgresql:///missing-host"],
)
def test_rejects_invalid_agent_postgres_dsn(dsn: str) -> None:
    with pytest.raises(ValidationError, match="agent_postgres_dsn"):
        Settings(
            llm_api_key="test-key",
            agent_postgres_dsn=dsn,
            _env_file=None,
        )


@pytest.mark.parametrize("version", ["", "contains spaces", "/unsafe"])
def test_rejects_invalid_index_pipeline_version(version: str) -> None:
    with pytest.raises(ValidationError, match="rag_index_pipeline_version"):
        Settings(
            llm_api_key="test-key",
            rag_index_pipeline_version=version,
            _env_file=None,
        )


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_tavily_provider_requires_non_blank_api_key(
    api_key: str | None,
) -> None:
    with pytest.raises(ValidationError, match="tavily_api_key"):
        Settings(
            llm_api_key="test-key",
            web_search_provider="tavily",
            tavily_api_key=api_key,
            _env_file=None,
        )


def test_disabled_web_search_allows_blank_api_key() -> None:
    settings = Settings(
        llm_api_key="test-key",
        web_search_provider="disabled",
        tavily_api_key="",
        _env_file=None,
    )

    assert settings.web_search_provider is WebSearchProviderName.DISABLED


@pytest.mark.parametrize(
    "redis_url",
    ["", "http://127.0.0.1:6379/0", "redis:///0", "not-a-url"],
)
def test_rejects_invalid_redis_url(redis_url: str) -> None:
    with pytest.raises(ValidationError, match="redis_url"):
        Settings(
            llm_api_key="test-key",
            redis_url=redis_url,
            _env_file=None,
        )


@pytest.mark.parametrize("namespace", ["", "contains spaces", "contains/prompt"])
def test_rejects_unsafe_cache_namespace(namespace: str) -> None:
    with pytest.raises(ValidationError, match="llm_cache_namespace"):
        Settings(
            llm_api_key="test-key",
            llm_cache_namespace=namespace,
            _env_file=None,
        )
