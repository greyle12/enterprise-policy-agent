from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.cache import CacheProviderName, CachedLLMClient
from app.llm.client import ChatMessage
from app.performance.concurrency import (
    ConcurrencyLoadRunner,
    ConcurrencyLoadScenario,
    ConcurrencyObservedMetrics,
)
from app.performance.models import ConcurrencyLoadReport, ConcurrencyLoadScenarioName


@dataclass(slots=True)
class _OfflineCache:
    provider: CacheProviderName = CacheProviderName.REDIS
    enabled: bool = True
    values: dict[str, str] = field(default_factory=dict)

    async def get(self, digest: str) -> str | None:
        return self.values.get(digest)

    async def set(self, digest: str, value: str, *, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self.values[digest] = value

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


@dataclass(slots=True)
class _LatencyLLM:
    latency_seconds: float
    calls: int = 0
    active: int = 0
    peak_active: int = 0

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        self.calls += 1
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            await asyncio.sleep(self.latency_seconds)
            return f"离线回答：{messages[-1]['content']}"
        finally:
            self.active -= 1

    async def close(self) -> None:
        return None


def _messages(key: str) -> tuple[ChatMessage, ...]:
    return (
        {"role": "system", "content": "只根据企业制度回答。"},
        {"role": "user", "content": f"Day 25 离线并发问题：{key}"},
    )


def _build_scenario(
    *,
    name: ConcurrencyLoadScenarioName,
    description: str,
    request_count: int,
    concurrency: int,
    unique_request_keys: int,
    provider_latency_ms: float,
    key_for_request: Callable[[int], str],
) -> ConcurrencyLoadScenario:
    cache = _OfflineCache()
    upstream = _LatencyLLM(latency_seconds=provider_latency_ms / 1_000)
    client = CachedLLMClient(
        upstream=upstream,
        backend=cache,
        identity=f"day25-offline-{name.value}-v1",
        ttl_seconds=600,
        max_request_bytes=16_384,
        singleflight_enabled=True,
        singleflight_max_keys=max(128, request_count),
    )

    async def operation(request_index: int) -> object:
        return await client.chat(_messages(key_for_request(request_index)))

    async def observe() -> ConcurrencyObservedMetrics:
        status = await client.cache_status()
        return ConcurrencyObservedMetrics(
            upstream_calls=upstream.calls,
            provider_peak_in_flight=upstream.peak_active,
            cache_hits=status.metrics.hits,
            coalesced_requests=status.metrics.coalesced,
        )

    return ConcurrencyLoadScenario(
        name=name,
        description=description,
        request_count=request_count,
        concurrency=concurrency,
        unique_request_keys=unique_request_keys,
        expected_upstream_calls=unique_request_keys,
        operation=operation,
        observe=observe,
        close=client.close,
    )


async def run_offline_concurrency_load(
    *,
    request_count: int = 24,
    concurrency: int = 12,
    provider_latency_ms: float = 15.0,
) -> ConcurrencyLoadReport:
    """Run three isolated request distributions without Redis, LLM, or network I/O."""

    if request_count < 1:
        raise ValueError("request_count must be greater than zero")
    if concurrency < 1:
        raise ValueError("concurrency must be greater than zero")
    if provider_latency_ms <= 0:
        raise ValueError("provider_latency_ms must be greater than zero")

    hotset_size = min(4, request_count)
    scenarios = (
        _build_scenario(
            name=ConcurrencyLoadScenarioName.HOT_KEY_BURST,
            description=("所有请求使用同一缓存键，验证 single-flight 与后续 cache hit。"),
            request_count=request_count,
            concurrency=concurrency,
            unique_request_keys=1,
            provider_latency_ms=provider_latency_ms,
            key_for_request=lambda _request_index: "travel-policy-hot-key",
        ),
        _build_scenario(
            name=ConcurrencyLoadScenarioName.MIXED_HOTSET,
            description=("请求均匀落在四个热点键上，验证按键合并且不同键保持并发。"),
            request_count=request_count,
            concurrency=concurrency,
            unique_request_keys=hotset_size,
            provider_latency_ms=provider_latency_ms,
            key_for_request=lambda request_index: f"policy-hotset-{request_index % hotset_size}",
        ),
        _build_scenario(
            name=ConcurrencyLoadScenarioName.UNIQUE_KEY_FANOUT,
            description=("每个请求使用不同键，观察无去重时 Provider 的并发扇出。"),
            request_count=request_count,
            concurrency=concurrency,
            unique_request_keys=request_count,
            provider_latency_ms=provider_latency_ms,
            key_for_request=lambda request_index: f"unique-policy-{request_index}",
        ),
    )
    runner = ConcurrencyLoadRunner(
        scenarios=scenarios,
        simulated_provider_latency_ms=provider_latency_ms,
    )
    return await runner.run()
