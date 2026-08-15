from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from app.cache.backends import (
    CacheValueRejectedError,
    LLMCacheBackend,
)
from app.cache.models import (
    CacheStateName,
    LLMCacheMetricsSnapshot,
    LLMCacheStatus,
)
from app.cache.singleflight import AsyncSingleFlight, SingleFlightRole
from app.llm.client import ChatMessage, LLMClient
from app.memory.conversation import sanitize_memory_content

logger = logging.getLogger(__name__)

_CACHE_KEY_SCHEMA = "enterprise-policy-agent/llm-response-cache/v1"


class ClosableLLMClient(LLMClient, Protocol):
    """LLM client that owns an async transport."""

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CacheKeyDecision:
    """Eligibility result produced before any cache I/O."""

    digest: str | None
    bypass_reason: str | None


@dataclass(slots=True)
class _MutableCacheMetrics:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    bypasses: int = 0
    errors: int = 0
    coalesced: int = 0
    singleflight_overflows: int = 0

    def snapshot(self) -> LLMCacheMetricsSnapshot:
        return LLMCacheMetricsSnapshot(
            hits=self.hits,
            misses=self.misses,
            writes=self.writes,
            bypasses=self.bypasses,
            errors=self.errors,
            coalesced=self.coalesced,
            singleflight_overflows=self.singleflight_overflows,
        )


def build_llm_cache_identity(
    *,
    base_url: str,
    model: str,
) -> str:
    """Build a secret-free identity that invalidates entries after model changes."""

    normalized_base_url = base_url.strip().rstrip("/")
    normalized_model = model.strip()
    if not normalized_base_url:
        raise ValueError("base_url must not be blank")
    if not normalized_model:
        raise ValueError("model must not be blank")
    canonical = json.dumps(
        {
            "adapter": "openai-compatible",
            "base_url": normalized_base_url,
            "model": normalized_model,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_llm_cache_key(
    messages: Sequence[ChatMessage],
    *,
    identity: str,
    max_request_bytes: int,
) -> CacheKeyDecision:
    """Create a prompt-free Redis digest or explain why the request must bypass."""

    if not identity.strip():
        raise ValueError("identity must not be blank")
    if max_request_bytes < 1024:
        raise ValueError("max_request_bytes must be at least 1024")
    if not messages:
        return CacheKeyDecision(digest=None, bypass_reason="empty_messages")

    canonical_messages: list[dict[str, str]] = []
    content_bytes = 0
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            return CacheKeyDecision(digest=None, bypass_reason="invalid_message")
        if not content.strip():
            return CacheKeyDecision(digest=None, bypass_reason="blank_message")
        content_bytes += len(content.encode("utf-8"))
        if content_bytes > max_request_bytes:
            return CacheKeyDecision(digest=None, bypass_reason="request_too_large")

        _, redacted, _ = sanitize_memory_content(
            content,
            character_limit=max(32, len(content)),
        )
        if redacted:
            return CacheKeyDecision(digest=None, bypass_reason="sensitive_content")
        canonical_messages.append({"role": role, "content": content})

    canonical = json.dumps(
        {
            "schema": _CACHE_KEY_SCHEMA,
            "identity": identity,
            "messages": canonical_messages,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return CacheKeyDecision(
        digest=sha256(canonical.encode("utf-8")).hexdigest(),
        bypass_reason=None,
    )


class CachedLLMClient:
    """Exact-request LLM response cache with fail-open Redis behavior."""

    def __init__(
        self,
        *,
        upstream: ClosableLLMClient,
        backend: LLMCacheBackend,
        identity: str,
        ttl_seconds: int,
        max_request_bytes: int,
        singleflight_enabled: bool = True,
        singleflight_max_keys: int = 128,
    ) -> None:
        if not identity.strip():
            raise ValueError("identity must not be blank")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if max_request_bytes < 1024:
            raise ValueError("max_request_bytes must be at least 1024")
        if singleflight_max_keys < 1:
            raise ValueError("singleflight_max_keys must be at least one")
        self._upstream = upstream
        self._backend = backend
        self._identity = identity
        self._ttl_seconds = ttl_seconds
        self._max_request_bytes = max_request_bytes
        self._singleflight_enabled = singleflight_enabled
        self._singleflight = AsyncSingleFlight[str](
            max_keys=singleflight_max_keys,
        )
        self._metrics = _MutableCacheMetrics()

    async def _fetch_and_maybe_cache(
        self,
        messages: Sequence[ChatMessage],
        *,
        digest: str,
        cache_read_succeeded: bool,
    ) -> str:
        response = await self._upstream.chat(messages)
        if not cache_read_succeeded or not response.strip():
            return response

        try:
            await self._backend.set(
                digest,
                response,
                ttl_seconds=self._ttl_seconds,
            )
        except CacheValueRejectedError:
            self._metrics.bypasses += 1
        except Exception as error:
            self._metrics.errors += 1
            logger.warning(
                "LLM cache write failed; returning upstream response",
                extra={"cache_error_type": type(error).__name__},
            )
        else:
            self._metrics.writes += 1
        return response

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        if not self._backend.enabled:
            self._metrics.bypasses += 1
            return await self._upstream.chat(messages)

        decision = build_llm_cache_key(
            messages,
            identity=self._identity,
            max_request_bytes=self._max_request_bytes,
        )
        if decision.digest is None:
            self._metrics.bypasses += 1
            return await self._upstream.chat(messages)

        cache_read_succeeded = False
        try:
            cached = await self._backend.get(decision.digest)
            cache_read_succeeded = True
        except CacheValueRejectedError:
            self._metrics.errors += 1
            logger.warning("LLM cache contained an invalid value; using upstream LLM")
            cached = None
        except Exception as error:
            self._metrics.errors += 1
            logger.warning(
                "LLM cache read failed; using upstream LLM",
                extra={"cache_error_type": type(error).__name__},
            )
            cached = None

        if cached is not None:
            self._metrics.hits += 1
            return cached
        if cache_read_succeeded:
            self._metrics.misses += 1

        async def operation() -> str:
            return await self._fetch_and_maybe_cache(
                messages,
                digest=decision.digest,
                cache_read_succeeded=cache_read_succeeded,
            )

        if not self._singleflight_enabled:
            return await operation()

        outcome = await self._singleflight.run(decision.digest, operation)
        if outcome.role is SingleFlightRole.FOLLOWER:
            self._metrics.coalesced += 1
        elif outcome.role is SingleFlightRole.OVERFLOW:
            self._metrics.singleflight_overflows += 1
        return outcome.value

    async def cache_status(self) -> LLMCacheStatus:
        if not self._backend.enabled:
            return LLMCacheStatus(
                provider=self._backend.provider,
                state=CacheStateName.DISABLED,
                available=False,
                ttl_seconds=self._ttl_seconds,
                singleflight_enabled=False,
                singleflight_max_keys=self._singleflight.max_keys,
                singleflight_in_flight=self._singleflight.in_flight,
                metrics=self._metrics.snapshot(),
            )

        try:
            available = await self._backend.ping()
        except Exception as error:
            logger.warning(
                "LLM cache status probe failed",
                extra={"cache_error_type": type(error).__name__},
            )
            available = False
        return LLMCacheStatus(
            provider=self._backend.provider,
            state=(CacheStateName.AVAILABLE if available else CacheStateName.DEGRADED),
            available=available,
            ttl_seconds=self._ttl_seconds,
            singleflight_enabled=self._singleflight_enabled,
            singleflight_max_keys=self._singleflight.max_keys,
            singleflight_in_flight=self._singleflight.in_flight,
            metrics=self._metrics.snapshot(),
        )

    async def close(self) -> None:
        await self._singleflight.aclose()
        try:
            await self._backend.aclose()
        except Exception as error:
            logger.warning(
                "LLM cache close failed",
                extra={"cache_error_type": type(error).__name__},
            )
        await self._upstream.close()
