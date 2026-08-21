from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.cache import CacheProviderName, CachedLLMClient
from app.llm import ConcurrencyLimitedLLMClient
from app.llm.client import ChatMessage

_MESSAGES: tuple[ChatMessage, ...] = ({"role": "user", "content": "差旅住宿标准是多少？"},)


@dataclass
class MemoryCache:
    provider: CacheProviderName = CacheProviderName.REDIS
    enabled: bool = True
    values: dict[str, str] = field(default_factory=dict)
    get_calls: int = 0

    async def get(self, digest: str) -> str | None:
        self.get_calls += 1
        return self.values.get(digest)

    async def set(self, digest: str, value: str, *, ttl_seconds: int) -> None:
        del ttl_seconds
        self.values[digest] = value

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


@dataclass
class CountingLLMClient:
    calls: int = 0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        del messages
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return "每晚 500 元。"

    async def close(self) -> None:
        return None


def _compose(
    raw: CountingLLMClient,
    backend: MemoryCache,
) -> tuple[CachedLLMClient, ConcurrencyLimitedLLMClient]:
    limiter = ConcurrencyLimitedLLMClient(
        upstream=raw,
        enabled=True,
        max_concurrency=1,
        max_queue=0,
        queue_timeout_seconds=1,
    )
    cached = CachedLLMClient(
        upstream=limiter,
        backend=backend,
        identity="provider-cache-composition-v1",
        ttl_seconds=600,
        max_request_bytes=4096,
        singleflight_enabled=True,
        singleflight_max_keys=16,
    )
    return cached, limiter


async def test_cache_hit_does_not_consume_provider_capacity() -> None:
    raw = CountingLLMClient()
    raw.release.set()
    cached, limiter = _compose(raw, MemoryCache())

    assert await cached.chat(_MESSAGES) == "每晚 500 元。"
    assert await cached.chat(_MESSAGES) == "每晚 500 元。"

    status = await limiter.status()
    assert raw.calls == 1
    assert status.metrics.requests == 1
    assert status.metrics.completed == 1
    await cached.close()


async def test_singleflight_followers_do_not_consume_provider_capacity() -> None:
    raw = CountingLLMClient()
    backend = MemoryCache()
    cached, limiter = _compose(raw, backend)
    requests = [asyncio.create_task(cached.chat(_MESSAGES)) for _ in range(8)]
    await raw.started.wait()

    async with asyncio.timeout(1):
        while backend.get_calls < len(requests):
            await asyncio.sleep(0)
    raw.release.set()

    assert await asyncio.gather(*requests) == ["每晚 500 元。"] * 8
    limiter_status = await limiter.status()
    cache_status = await cached.cache_status()
    assert raw.calls == 1
    assert limiter_status.metrics.requests == 1
    assert limiter_status.metrics.rejected == 0
    assert cache_status.metrics.coalesced == 7
    await cached.close()
