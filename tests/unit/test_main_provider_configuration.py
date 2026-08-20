from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

from app import main as main_module
from app.llm import ConcurrencyLimitedLLMClient, ProviderLimiterStateName
from app.llm.client import ChatMessage
from app.rag.reranking import RerankerProviderName


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
