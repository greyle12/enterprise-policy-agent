from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

from app import main as main_module
from app.llm import ConcurrencyLimitedLLMClient, ProviderLimiterStateName
from app.llm.client import ChatMessage


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
