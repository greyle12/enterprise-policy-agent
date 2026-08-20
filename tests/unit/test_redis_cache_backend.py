from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.cache import CacheValueRejectedError, RedisLLMCache

_DIGEST = "a" * 64


@dataclass
class FakeRedisClient:
    values: dict[str, object] = field(default_factory=dict)
    set_calls: list[tuple[str, str, int]] = field(default_factory=list)
    ping_result: object = True
    closed: bool = False

    async def get(self, name: str) -> object:
        return self.values.get(name)

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
    ) -> object:
        self.values[name] = value
        self.set_calls.append((name, value, ex))
        return True

    async def ping(self) -> object:
        return self.ping_result

    async def aclose(self) -> None:
        self.closed = True


def build_cache(client: FakeRedisClient, *, max_value_bytes: int = 4096) -> RedisLLMCache:
    return RedisLLMCache(
        client=client,
        namespace="agent:llm:v1",
        max_value_bytes=max_value_bytes,
    )


@pytest.mark.parametrize("namespace", ["", "has spaces", "contains/prompt", "a" * 97])
def test_rejects_unsafe_namespace(namespace: str) -> None:
    with pytest.raises(ValueError, match="namespace"):
        RedisLLMCache(
            client=FakeRedisClient(),
            namespace=namespace,
            max_value_bytes=4096,
        )


async def test_round_trip_uses_namespaced_digest_and_explicit_ttl() -> None:
    client = FakeRedisClient()
    cache = build_cache(client)

    await cache.set(_DIGEST, "制度答案", ttl_seconds=600)

    assert client.set_calls == [(f"agent:llm:v1:{_DIGEST}", "制度答案", 600)]
    assert await cache.get(_DIGEST) == "制度答案"


async def test_get_accepts_valid_utf8_bytes() -> None:
    client = FakeRedisClient(values={f"agent:llm:v1:{_DIGEST}": "答案".encode()})

    assert await build_cache(client).get(_DIGEST) == "答案"


@pytest.mark.parametrize("value", ["", "   ", object(), b"\xff"])
async def test_get_rejects_corrupt_values(value: object) -> None:
    client = FakeRedisClient(values={f"agent:llm:v1:{_DIGEST}": value})

    with pytest.raises(CacheValueRejectedError):
        await build_cache(client).get(_DIGEST)


async def test_set_rejects_blank_or_oversized_values() -> None:
    cache = build_cache(FakeRedisClient(), max_value_bytes=1024)

    with pytest.raises(CacheValueRejectedError):
        await cache.set(_DIGEST, "", ttl_seconds=600)
    with pytest.raises(CacheValueRejectedError):
        await cache.set(_DIGEST, "中" * 400, ttl_seconds=600)


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "policy question"])
async def test_rejects_non_sha256_cache_keys(digest: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        await build_cache(FakeRedisClient()).get(digest)


async def test_ping_and_close_delegate_to_redis_client() -> None:
    client = FakeRedisClient()
    cache = build_cache(client)

    assert await cache.ping() is True
    await cache.aclose()

    assert client.closed is True


async def test_from_url_builds_lazy_redis_py_client_without_connecting() -> None:
    cache = RedisLLMCache.from_url(
        url="redis://127.0.0.1:6379/0",
        namespace="agent:llm:v1",
        timeout_seconds=0.1,
        max_value_bytes=4096,
    )

    await cache.aclose()
