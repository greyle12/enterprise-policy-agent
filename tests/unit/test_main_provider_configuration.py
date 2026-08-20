from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app import main as main_module
from app.llm import ConcurrencyLimitedLLMClient, ProviderLimiterStateName
from app.llm.client import ChatMessage
from app.rag.reranking import RerankerProviderName
from app.rag.vector_index import InMemoryVectorIndex, VectorStoreProviderName


class FakeLLMClient:
    def __init__(self) -> None:
        self.closed = False

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        return messages[-1]["content"]

    async def close(self) -> None:
        self.closed = True


async def test_builds_provider_limiter_from_bounded_settings() -> None:
    upstream = FakeLLMClient()
    settings = SimpleNamespace(
        llm_provider_limit_enabled=True,
        llm_provider_max_concurrency=6,
        llm_provider_max_queue=24,
        llm_provider_queue_timeout_seconds=1.5,
    )

    limiter = main_module._build_llm_provider_limiter(settings, upstream)

    assert isinstance(limiter, ConcurrencyLimitedLLMClient)
    status = await limiter.status()
    assert status.enabled is True
    assert status.state is ProviderLimiterStateName.AVAILABLE
    assert status.max_concurrency == 6
    assert status.max_queue == 24
    assert status.queue_timeout_seconds == 1.5
    assert await limiter.chat([{"role": "user", "content": "hello"}]) == "hello"
    await limiter.close()
    assert upstream.closed is True


def test_reranker_is_disabled_without_explicit_provider() -> None:
    settings = SimpleNamespace(
        rag_reranker_provider=RerankerProviderName.DISABLED,
    )

    assert main_module._build_reranking_provider(settings) is None


def test_builds_bge_reranker_from_explicit_settings(monkeypatch) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_provider(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(main_module, "BGERerankingProvider", fake_provider)
    settings = SimpleNamespace(
        rag_reranker_provider=RerankerProviderName.BGE,
        rag_reranker_model_name="company/test-reranker",
        rag_reranker_device="cpu",
        rag_reranker_batch_size=12,
    )

    provider = main_module._build_reranking_provider(settings)

    assert provider is sentinel
    assert captured == {
        "model_name": "company/test-reranker",
        "device": "cpu",
        "batch_size": 12,
    }


def test_builds_memory_vector_index_by_default() -> None:
    settings = SimpleNamespace(
        rag_vector_store_provider=VectorStoreProviderName.MEMORY,
    )

    index = main_module._build_policy_vector_index(settings, dimension=512)

    assert isinstance(index, InMemoryVectorIndex)
    assert index.dimension == 512


def test_builds_and_initializes_pgvector_index(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Index:
        initialized = False
        closed = False

        def initialize_schema(self) -> None:
            self.initialized = True

        def close(self) -> None:
            self.closed = True

    index = _Index()

    def from_dsn(dsn: str, **kwargs: object) -> _Index:
        captured["dsn"] = dsn
        captured.update(kwargs)
        return index

    monkeypatch.setattr(main_module.PgVectorIndex, "from_dsn", from_dsn)
    settings = SimpleNamespace(
        rag_vector_store_provider=VectorStoreProviderName.PGVECTOR,
        rag_pgvector_dsn=SecretStr("postgresql://user:secret@postgres/policies"),
        rag_pgvector_collection="policy-v1",
        rag_pgvector_min_pool_size=2,
        rag_pgvector_max_pool_size=6,
        rag_pgvector_connect_timeout_seconds=4.0,
    )

    result = main_module._build_policy_vector_index(settings, dimension=512)

    assert result is index
    assert index.initialized is True
    assert captured == {
        "dsn": "postgresql://user:secret@postgres/policies",
        "dimension": 512,
        "collection_name": "policy-v1",
        "min_pool_size": 2,
        "max_pool_size": 6,
        "connect_timeout_seconds": 4.0,
    }
