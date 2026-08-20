from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.cache import CacheProviderName, CachedLLMClient
from app.llm.client import ChatMessage

_REQUEST: tuple[ChatMessage, ...] = (
    {"role": "system", "content": "只根据企业制度回答。"},
    {"role": "user", "content": "差旅住宿标准是多少？"},
)


@dataclass
class _OfflineCache:
    provider: CacheProviderName = CacheProviderName.REDIS
    enabled: bool = True
    values: dict[str, str] = field(default_factory=dict)
    reads: int = 0
    writes: int = 0
    closed: bool = False

    async def get(self, digest: str) -> str | None:
        self.reads += 1
        return self.values.get(digest)

    async def set(self, digest: str, value: str, *, ttl_seconds: int) -> None:
        if ttl_seconds != 600:
            raise ValueError("offline fixture expected the Day 24 TTL")
        self.values[digest] = value
        self.writes += 1

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class _BlockingLLM:
    answer: str = "住宿标准为每晚 500 元。"
    calls: int = 0
    closed: bool = False
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        del messages
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.answer

    async def close(self) -> None:
        self.closed = True


@dataclass
class _ConcurrencyProbeLLM:
    calls: int = 0
    active: int = 0
    max_active: int = 0
    closed: bool = False
    both_started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.calls == 2:
            self.both_started.set()
        try:
            await self.release.wait()
            return messages[-1]["content"]
        finally:
            self.active -= 1

    async def close(self) -> None:
        self.closed = True


def _client(
    *,
    cache: _OfflineCache,
    llm: _BlockingLLM | _ConcurrencyProbeLLM,
) -> CachedLLMClient:
    return CachedLLMClient(
        upstream=llm,
        backend=cache,
        identity="day24-offline-model-v1",
        ttl_seconds=600,
        max_request_bytes=16_384,
        singleflight_enabled=True,
        singleflight_max_keys=128,
    )


async def _wait_for_reads(cache: _OfflineCache, expected: int) -> None:
    for _ in range(200):
        if cache.reads >= expected:
            return
        await asyncio.sleep(0)
    raise RuntimeError("offline requests did not reach the cache in time")


async def _run_verification() -> dict[str, object]:
    request_count = 12
    cache = _OfflineCache()
    llm = _BlockingLLM()
    client = _client(cache=cache, llm=llm)

    requests = [asyncio.create_task(client.chat(_REQUEST)) for _ in range(request_count)]
    await llm.started.wait()
    await _wait_for_reads(cache, request_count)
    active_status = await client.cache_status()
    llm.release.set()
    answers = await asyncio.gather(*requests)
    completed_status = await client.cache_status()
    cached_answer = await client.chat(_REQUEST)
    cached_status = await client.cache_status()

    cancellation_cache = _OfflineCache()
    cancellation_llm = _BlockingLLM(answer="取消隔离成功")
    cancellation_client = _client(cache=cancellation_cache, llm=cancellation_llm)
    cancelled_waiter = asyncio.create_task(cancellation_client.chat(_REQUEST))
    await cancellation_llm.started.wait()
    surviving_waiter = asyncio.create_task(cancellation_client.chat(_REQUEST))
    await _wait_for_reads(cancellation_cache, 2)
    cancelled_waiter.cancel()
    try:
        await cancelled_waiter
    except asyncio.CancelledError:
        cancellation_observed = True
    else:
        cancellation_observed = False
    cancellation_llm.release.set()
    surviving_answer = await surviving_waiter
    cancellation_status = await cancellation_client.cache_status()

    independent_cache = _OfflineCache()
    independent_llm = _ConcurrencyProbeLLM()
    independent_client = _client(cache=independent_cache, llm=independent_llm)
    first_question: tuple[ChatMessage, ...] = ({"role": "user", "content": "差旅制度是什么？"},)
    second_question: tuple[ChatMessage, ...] = ({"role": "user", "content": "采购制度是什么？"},)
    independent_requests = (
        asyncio.create_task(independent_client.chat(first_question)),
        asyncio.create_task(independent_client.chat(second_question)),
    )
    await asyncio.wait_for(independent_llm.both_started.wait(), timeout=1.0)
    independent_llm.release.set()
    independent_answers = await asyncio.gather(*independent_requests)

    checks = {
        "same_key_has_one_upstream_call": (
            llm.calls == 1 and answers == [llm.answer] * request_count
        ),
        "followers_reuse_leader_result": (completed_status.metrics.coalesced == request_count - 1),
        "leader_writes_once": cache.writes == 1,
        "in_flight_gauge_cleans_up": (
            active_status.singleflight_in_flight == 1
            and completed_status.singleflight_in_flight == 0
        ),
        "subsequent_request_hits_cache": (
            cached_answer == llm.answer and llm.calls == 1 and cached_status.metrics.hits == 1
        ),
        "cancelled_waiter_is_isolated": (
            cancellation_observed
            and surviving_answer == cancellation_llm.answer
            and cancellation_llm.calls == 1
            and cancellation_status.metrics.coalesced == 1
        ),
        "different_keys_remain_concurrent": (
            independent_llm.calls == 2
            and independent_llm.max_active == 2
            and independent_answers
            == [first_question[-1]["content"], second_question[-1]["content"]]
        ),
        "registry_did_not_overflow": (cached_status.metrics.singleflight_overflows == 0),
    }

    await client.close()
    await cancellation_client.close()
    await independent_client.close()
    checks["resources_closed"] = (
        cache.closed
        and llm.closed
        and cancellation_cache.closed
        and cancellation_llm.closed
        and independent_cache.closed
        and independent_llm.closed
    )

    return {
        "passed": all(checks.values()),
        "concurrent_requests": request_count,
        "upstream_calls": llm.calls,
        "coalesced_requests": completed_status.metrics.coalesced,
        "cache_writes": cache.writes,
        "checks": checks,
        "network_calls": False,
        "live_llm_calls": False,
    }


def run_verification() -> dict[str, object]:
    """Exercise the Day 24 async single-flight contract entirely offline."""

    return asyncio.run(_run_verification())


def main() -> int:
    report = run_verification()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
