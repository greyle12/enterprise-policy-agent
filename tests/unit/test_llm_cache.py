from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from app.cache import (
    CacheProviderName,
    CacheStateName,
    CachedLLMClient,
    DisabledLLMCache,
    build_llm_cache_identity,
    build_llm_cache_key,
)
from app.llm.client import ChatMessage

_IDENTITY = "model-identity-v1"
_MESSAGES: tuple[ChatMessage, ...] = (
    {"role": "system", "content": "只根据制度回答。"},
    {"role": "user", "content": "差旅住宿标准是多少？"},
)


@dataclass
class FakeLLMClient:
    response: str = "住宿标准为每晚 500 元。"
    error: Exception | None = None
    calls: list[tuple[ChatMessage, ...]] = field(default_factory=list)
    closed: bool = False

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(tuple(messages))
        if self.error is not None:
            raise self.error
        return self.response

    async def close(self) -> None:
        self.closed = True


@dataclass
class BlockingLLMClient:
    response: str = "住宿标准为每晚 500 元。"
    calls: int = 0
    closed: bool = False
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        del messages
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.response

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeCacheBackend:
    provider: CacheProviderName = CacheProviderName.REDIS
    enabled: bool = True
    values: dict[str, str] = field(default_factory=dict)
    get_calls: list[str] = field(default_factory=list)
    set_calls: list[tuple[str, str, int]] = field(default_factory=list)
    fail_get: bool = False
    fail_set: bool = False
    fail_ping: bool = False
    closed: bool = False

    async def get(self, digest: str) -> str | None:
        self.get_calls.append(digest)
        if self.fail_get:
            raise ConnectionError("redis unavailable")
        return self.values.get(digest)

    async def set(self, digest: str, value: str, *, ttl_seconds: int) -> None:
        if self.fail_set:
            raise ConnectionError("redis unavailable")
        self.values[digest] = value
        self.set_calls.append((digest, value, ttl_seconds))

    async def ping(self) -> bool:
        if self.fail_ping:
            raise ConnectionError("redis unavailable")
        return True

    async def aclose(self) -> None:
        self.closed = True


def build_client(
    *,
    upstream: FakeLLMClient | None = None,
    backend: FakeCacheBackend | DisabledLLMCache | None = None,
    max_request_bytes: int = 4096,
) -> tuple[CachedLLMClient, FakeLLMClient, FakeCacheBackend | DisabledLLMCache]:
    selected_upstream = upstream or FakeLLMClient()
    selected_backend = backend or FakeCacheBackend()
    client = CachedLLMClient(
        upstream=selected_upstream,
        backend=selected_backend,
        identity=_IDENTITY,
        ttl_seconds=600,
        max_request_bytes=max_request_bytes,
    )
    return client, selected_upstream, selected_backend


async def test_exact_request_is_cached_with_ttl() -> None:
    client, upstream, backend = build_client()

    first = await client.chat(_MESSAGES)
    second = await client.chat(_MESSAGES)
    status = await client.cache_status()

    assert first == second == "住宿标准为每晚 500 元。"
    assert len(upstream.calls) == 1
    assert isinstance(backend, FakeCacheBackend)
    assert len(backend.get_calls) == 2
    assert backend.set_calls[0][2] == 600
    assert status.metrics.hits == 1
    assert status.metrics.misses == 1
    assert status.metrics.writes == 1


async def test_concurrent_cache_misses_are_coalesced_into_one_llm_call() -> None:
    upstream = BlockingLLMClient()
    backend = FakeCacheBackend()
    client = CachedLLMClient(
        upstream=upstream,
        backend=backend,
        identity=_IDENTITY,
        ttl_seconds=600,
        max_request_bytes=4096,
        singleflight_enabled=True,
        singleflight_max_keys=16,
    )
    requests = [asyncio.create_task(client.chat(_MESSAGES)) for _ in range(10)]
    await upstream.started.wait()

    for _ in range(100):
        if len(backend.get_calls) == len(requests):
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("concurrent requests did not reach the cache in time")

    active_status = await client.cache_status()
    upstream.release.set()
    responses = await asyncio.gather(*requests)
    final_status = await client.cache_status()

    assert responses == [upstream.response] * len(requests)
    assert upstream.calls == 1
    assert len(backend.set_calls) == 1
    assert active_status.singleflight_in_flight == 1
    assert final_status.singleflight_enabled is True
    assert final_status.singleflight_max_keys == 16
    assert final_status.singleflight_in_flight == 0
    assert final_status.metrics.misses == 10
    assert final_status.metrics.writes == 1
    assert final_status.metrics.coalesced == 9
    assert final_status.metrics.singleflight_overflows == 0
    await client.close()


async def test_message_or_model_identity_change_invalidates_key() -> None:
    first = build_llm_cache_key(_MESSAGES, identity="model-a", max_request_bytes=4096)
    changed_message = build_llm_cache_key(
        ({"role": "user", "content": "采购标准是多少？"},),
        identity="model-a",
        max_request_bytes=4096,
    )
    changed_model = build_llm_cache_key(
        _MESSAGES,
        identity="model-b",
        max_request_bytes=4096,
    )

    assert first.digest is not None
    assert len(first.digest) == 64
    assert "差旅" not in first.digest
    assert len({first.digest, changed_message.digest, changed_model.digest}) == 3


def test_cache_identity_is_stable_and_normalizes_trailing_slash() -> None:
    first = build_llm_cache_identity(base_url="https://llm.example/v1/", model="demo")
    second = build_llm_cache_identity(base_url="https://llm.example/v1", model="demo")
    changed = build_llm_cache_identity(base_url="https://llm.example/v1", model="demo-v2")

    assert first == second
    assert first != changed
    assert "https://" not in first


async def test_sensitive_messages_bypass_cache() -> None:
    client, upstream, backend = build_client()
    sensitive: tuple[ChatMessage, ...] = (
        {"role": "user", "content": "请使用 api_key=super-secret-value 查询制度"},
    )

    await client.chat(sensitive)
    await client.chat(sensitive)
    status = await client.cache_status()

    assert len(upstream.calls) == 2
    assert isinstance(backend, FakeCacheBackend)
    assert backend.get_calls == []
    assert backend.set_calls == []
    assert status.metrics.bypasses == 2


async def test_oversized_request_bypasses_cache() -> None:
    client, upstream, backend = build_client(max_request_bytes=1024)
    messages: tuple[ChatMessage, ...] = ({"role": "user", "content": "a" * 1025},)

    await client.chat(messages)

    assert len(upstream.calls) == 1
    assert isinstance(backend, FakeCacheBackend)
    assert backend.get_calls == []


async def test_cache_read_failure_falls_back_without_second_redis_call() -> None:
    backend = FakeCacheBackend(fail_get=True)
    client, upstream, _ = build_client(backend=backend)

    result = await client.chat(_MESSAGES)
    status = await client.cache_status()

    assert result == upstream.response
    assert len(upstream.calls) == 1
    assert backend.set_calls == []
    assert status.metrics.errors == 1
    assert status.metrics.misses == 0


async def test_cache_write_failure_does_not_hide_upstream_response() -> None:
    backend = FakeCacheBackend(fail_set=True)
    client, upstream, _ = build_client(backend=backend)

    result = await client.chat(_MESSAGES)
    status = await client.cache_status()

    assert result == upstream.response
    assert status.metrics.misses == 1
    assert status.metrics.errors == 1


async def test_upstream_error_is_never_cached() -> None:
    upstream = FakeLLMClient(error=RuntimeError("model failed"))
    client, _, backend = build_client(upstream=upstream)

    with pytest.raises(RuntimeError, match="model failed"):
        await client.chat(_MESSAGES)

    assert isinstance(backend, FakeCacheBackend)
    assert backend.set_calls == []


async def test_disabled_provider_calls_upstream_without_cache_io() -> None:
    client, upstream, _ = build_client(backend=DisabledLLMCache())

    await client.chat(_MESSAGES)
    status = await client.cache_status()

    assert len(upstream.calls) == 1
    assert status.provider is CacheProviderName.DISABLED
    assert status.state is CacheStateName.DISABLED
    assert status.metrics.bypasses == 1


async def test_status_reports_redis_degradation_without_raising() -> None:
    client, _, _ = build_client(backend=FakeCacheBackend(fail_ping=True))

    status = await client.cache_status()

    assert status.state is CacheStateName.DEGRADED
    assert status.available is False


async def test_close_releases_cache_and_upstream() -> None:
    client, upstream, backend = build_client()

    await client.close()

    assert upstream.closed is True
    assert isinstance(backend, FakeCacheBackend)
    assert backend.closed is True
