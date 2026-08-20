from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.cache import CacheProviderName, CachedLLMClient, RedisLLMCache
from app.llm.client import ChatMessage


@dataclass
class _OfflineRedisClient:
    values: dict[str, object] = field(default_factory=dict)
    set_calls: list[tuple[str, str, int]] = field(default_factory=list)
    fail_reads: bool = False
    closed: bool = False

    async def get(self, name: str) -> object:
        if self.fail_reads:
            raise ConnectionError("offline Redis failure fixture")
        return self.values.get(name)

    async def set(self, name: str, value: str, *, ex: int) -> object:
        self.values[name] = value
        self.set_calls.append((name, value, ex))
        return True

    async def ping(self) -> object:
        return not self.fail_reads

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class _OfflineLLM:
    answer: str
    calls: int = 0
    closed: bool = False

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        del messages
        self.calls += 1
        return self.answer

    async def close(self) -> None:
        self.closed = True


def _cached_client(
    redis_client: _OfflineRedisClient,
    llm: _OfflineLLM,
) -> CachedLLMClient:
    return CachedLLMClient(
        upstream=llm,
        backend=RedisLLMCache(
            client=redis_client,
            namespace="day23:llm:v1",
            max_value_bytes=16_384,
        ),
        identity="offline-model-v1",
        ttl_seconds=600,
        max_request_bytes=16_384,
    )


async def _run_verification() -> dict[str, object]:
    redis_client = _OfflineRedisClient()
    llm = _OfflineLLM("住宿标准为每晚 500 元。")
    client = _cached_client(redis_client, llm)
    exact_request: tuple[ChatMessage, ...] = (
        {"role": "system", "content": "只根据企业制度回答。"},
        {"role": "user", "content": "差旅住宿标准是多少？"},
    )
    changed_request: tuple[ChatMessage, ...] = (
        {"role": "system", "content": "只根据企业制度回答。"},
        {"role": "user", "content": "采购审批标准是多少？"},
    )

    first = await client.chat(exact_request)
    second = await client.chat(exact_request)
    calls_after_hit = llm.calls
    await client.chat(changed_request)
    writes_before_sensitive = len(redis_client.set_calls)
    sensitive_request: tuple[ChatMessage, ...] = (
        {"role": "user", "content": "api_key=do-not-cache-this-value"},
    )
    await client.chat(sensitive_request)
    writes_after_sensitive = len(redis_client.set_calls)
    status = await client.cache_status()

    failed_redis = _OfflineRedisClient(fail_reads=True)
    fallback_llm = _OfflineLLM("Redis 故障时的直连答案")
    fallback_client = _cached_client(failed_redis, fallback_llm)
    fallback = await fallback_client.chat(exact_request)
    fallback_status = await fallback_client.cache_status()

    redis_keys = [key for key, _, _ in redis_client.set_calls]
    checks = {
        "exact_request_hit": first == second and calls_after_hit == 1,
        "changed_request_miss": llm.calls == 3,
        "ttl_applied": all(ttl == 600 for _, _, ttl in redis_client.set_calls),
        "prompt_absent_from_keys": all(
            "差旅" not in key and "采购" not in key and len(key.rsplit(":", 1)[-1]) == 64
            for key in redis_keys
        ),
        "sensitive_request_bypassed": writes_before_sensitive == writes_after_sensitive,
        "redis_failure_fails_open": fallback == fallback_llm.answer and fallback_llm.calls == 1,
        "redis_failure_not_written": failed_redis.set_calls == [],
        "metrics_observable": (
            status.provider is CacheProviderName.REDIS
            and status.metrics.hits == 1
            and status.metrics.misses == 2
            and status.metrics.writes == 2
            and status.metrics.bypasses == 1
            and status.metrics.errors == 0
            and status.metrics.coalesced == 0
            and status.metrics.singleflight_overflows == 0
        ),
        "degraded_status_observable": (
            fallback_status.available is False and fallback_status.metrics.errors == 1
        ),
    }
    await client.close()
    await fallback_client.close()
    checks["resources_closed"] = (
        redis_client.closed and failed_redis.closed and llm.closed and fallback_llm.closed
    )
    return {
        "passed": all(checks.values()),
        "provider": status.provider.value,
        "state": status.state.value,
        "ttl_seconds": status.ttl_seconds,
        "metrics": {
            "hits": status.metrics.hits,
            "misses": status.metrics.misses,
            "writes": status.metrics.writes,
            "bypasses": status.metrics.bypasses,
            "errors": status.metrics.errors,
            "coalesced": status.metrics.coalesced,
            "singleflight_overflows": status.metrics.singleflight_overflows,
        },
        "checks": checks,
        "network_calls": False,
        "live_llm_calls": False,
    }


def run_verification() -> dict[str, object]:
    """Exercise the Day 23 cache contract with deterministic offline doubles."""

    return asyncio.run(_run_verification())


def main() -> int:
    report = run_verification()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
