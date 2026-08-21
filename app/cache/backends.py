from __future__ import annotations

import re
from typing import Protocol, Self

from app.cache.models import CacheProviderName

_CACHE_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CACHE_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9:_-]{1,96}$")


class CacheBackendError(RuntimeError):
    """Raised when a cache operation cannot be completed safely."""


class CacheValueRejectedError(CacheBackendError):
    """Raised when a cache value violates the bounded storage contract."""


class AsyncRedisClient(Protocol):
    """Small redis-py surface used by the cache backend and unit tests."""

    async def get(self, name: str) -> object: ...

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
    ) -> object: ...

    async def ping(self) -> object: ...

    async def aclose(self) -> None: ...


class LLMCacheBackend(Protocol):
    """Storage contract required by the cached LLM decorator."""

    provider: CacheProviderName
    enabled: bool

    async def get(self, digest: str) -> str | None: ...

    async def set(
        self,
        digest: str,
        value: str,
        *,
        ttl_seconds: int,
    ) -> None: ...

    async def ping(self) -> bool: ...

    async def aclose(self) -> None: ...


class DisabledLLMCache:
    """No-op backend used when caching is intentionally disabled."""

    provider = CacheProviderName.DISABLED
    enabled = False

    async def get(self, digest: str) -> str | None:
        del digest
        return None

    async def set(
        self,
        digest: str,
        value: str,
        *,
        ttl_seconds: int,
    ) -> None:
        del digest, value, ttl_seconds

    async def ping(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class RedisLLMCache:
    """Bounded async Redis storage for successful LLM text responses."""

    provider = CacheProviderName.REDIS
    enabled = True

    def __init__(
        self,
        *,
        client: AsyncRedisClient,
        namespace: str,
        max_value_bytes: int,
    ) -> None:
        normalized_namespace = namespace.strip()
        if _CACHE_NAMESPACE_PATTERN.fullmatch(normalized_namespace) is None:
            raise ValueError("namespace contains unsupported characters or is too long")
        if max_value_bytes < 1024:
            raise ValueError("max_value_bytes must be at least 1024")
        self._client = client
        self._namespace = normalized_namespace
        self._max_value_bytes = max_value_bytes

    @classmethod
    def from_url(
        cls,
        *,
        url: str,
        namespace: str,
        timeout_seconds: float,
        max_value_bytes: int,
    ) -> Self:
        """Create an async redis-py client without connecting at startup."""

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        from redis.asyncio import Redis

        client = Redis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
            retry_on_timeout=False,
            health_check_interval=30,
            max_connections=20,
            client_name="enterprise-policy-agent-llm-cache",
        )
        return cls(
            client=client,
            namespace=namespace,
            max_value_bytes=max_value_bytes,
        )

    def _key(self, digest: str) -> str:
        if _CACHE_DIGEST_PATTERN.fullmatch(digest) is None:
            raise ValueError("cache digest must be a lowercase SHA-256 hex value")
        return f"{self._namespace}:{digest}"

    async def get(self, digest: str) -> str | None:
        value = await self._client.get(self._key(digest))
        if value is None:
            return None
        if isinstance(value, bytes):
            if len(value) > self._max_value_bytes:
                raise CacheValueRejectedError("cached value exceeds the configured limit")
            try:
                decoded = value.decode("utf-8")
            except UnicodeDecodeError as error:
                raise CacheValueRejectedError("cached value is not valid UTF-8") from error
        elif isinstance(value, str):
            decoded = value
        else:
            raise CacheValueRejectedError("cached value must be text")

        if not decoded.strip():
            raise CacheValueRejectedError("cached value must not be blank")
        if len(decoded.encode("utf-8")) > self._max_value_bytes:
            raise CacheValueRejectedError("cached value exceeds the configured limit")
        return decoded

    async def set(
        self,
        digest: str,
        value: str,
        *,
        ttl_seconds: int,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if not value.strip():
            raise CacheValueRejectedError("cache value must not be blank")
        if len(value.encode("utf-8")) > self._max_value_bytes:
            raise CacheValueRejectedError("cache value exceeds the configured limit")
        await self._client.set(
            self._key(digest),
            value,
            ex=ttl_seconds,
        )

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def aclose(self) -> None:
        await self._client.aclose()
