from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from app.llm import (
    ConcurrencyLimitedLLMClient,
    ProviderLimiterClosedError,
    ProviderLimiterStateName,
    ProviderOverloadedError,
    ProviderQueueTimeoutError,
)
from app.llm.client import ChatMessage


class BlockingLLMClient:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started: list[str] = []
        self.active = 0
        self.peak_active = 0
        self.closed_count = 0

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        label = messages[-1]["content"]
        self.started.append(label)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            await self.release.wait()
            return f"answer:{label}"
        finally:
            self.active -= 1

    async def close(self) -> None:
        self.closed_count += 1


class FailingLLMClient:
    def __init__(self) -> None:
        self.calls = 0
        self.closed_count = 0

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        del messages
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("private upstream detail")
        return "recovered"

    async def close(self) -> None:
        self.closed_count += 1


def _messages(label: str) -> list[ChatMessage]:
    return [{"role": "user", "content": label}]


async def _wait_for_started(upstream: BlockingLLMClient, count: int) -> None:
    async with asyncio.timeout(1):
        while len(upstream.started) < count:
            await asyncio.sleep(0)


async def _wait_for_queue(
    client: ConcurrencyLimitedLLMClient,
    count: int,
) -> None:
    async with asyncio.timeout(1):
        while (await client.status()).queued != count:
            await asyncio.sleep(0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_concurrency": 0}, "max_concurrency"),
        ({"max_queue": -1}, "max_queue"),
        ({"queue_timeout_seconds": 0}, "queue_timeout_seconds"),
    ],
)
def test_rejects_invalid_limits(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ConcurrencyLimitedLLMClient(upstream=BlockingLLMClient(), **kwargs)


async def test_disabled_limiter_preserves_unbounded_compatibility() -> None:
    upstream = BlockingLLMClient()
    client = ConcurrencyLimitedLLMClient(
        upstream=upstream,
        enabled=False,
        max_concurrency=1,
        max_queue=0,
    )

    first = asyncio.create_task(client.chat(_messages("first")))
    second = asyncio.create_task(client.chat(_messages("second")))
    await _wait_for_started(upstream, 2)

    status = await client.status()
    assert status.state is ProviderLimiterStateName.DISABLED
    assert status.in_flight == 2
    assert status.queued == 0
    assert status.metrics.bypassed == 2
    assert status.metrics.rejected == 0

    upstream.release.set()
    assert await asyncio.gather(first, second) == ["answer:first", "answer:second"]
    await client.close()


async def test_bounds_active_calls_and_rejects_a_full_fifo_queue() -> None:
    upstream = BlockingLLMClient()
    client = ConcurrencyLimitedLLMClient(
        upstream=upstream,
        enabled=True,
        max_concurrency=2,
        max_queue=2,
        queue_timeout_seconds=1,
    )

    tasks = [
        asyncio.create_task(client.chat(_messages(label)))
        for label in ("first", "second")
    ]
    await _wait_for_started(upstream, 2)
    tasks.extend(
        asyncio.create_task(client.chat(_messages(label)))
        for label in ("third", "fourth")
    )
    await _wait_for_queue(client, 2)

    with pytest.raises(ProviderOverloadedError):
        await client.chat(_messages("rejected"))
    saturated = await client.status()
    assert saturated.state is ProviderLimiterStateName.SATURATED
    assert saturated.in_flight == 2
    assert saturated.queued == 2

    upstream.release.set()
    assert await asyncio.gather(*tasks) == [
        "answer:first",
        "answer:second",
        "answer:third",
        "answer:fourth",
    ]
    assert upstream.started == ["first", "second", "third", "fourth"]
    assert upstream.peak_active == 2

    status = await client.status()
    assert status.state is ProviderLimiterStateName.AVAILABLE
    assert status.metrics.requests == 5
    assert status.metrics.accepted == 4
    assert status.metrics.started == 4
    assert status.metrics.completed == 4
    assert status.metrics.rejected == 1
    assert status.metrics.peak_in_flight == 2
    assert status.metrics.peak_queued == 2
    assert status.metrics.average_wait_ms > 0
    await client.close()


async def test_times_out_queued_work_without_leaking_capacity() -> None:
    upstream = BlockingLLMClient()
    client = ConcurrencyLimitedLLMClient(
        upstream=upstream,
        enabled=True,
        max_concurrency=1,
        max_queue=1,
        queue_timeout_seconds=0.01,
    )

    active = asyncio.create_task(client.chat(_messages("active")))
    await _wait_for_started(upstream, 1)
    with pytest.raises(ProviderQueueTimeoutError):
        await client.chat(_messages("timeout"))

    status = await client.status()
    assert status.in_flight == 1
    assert status.queued == 0
    assert status.metrics.timed_out == 1
    assert isinstance(ProviderQueueTimeoutError(), TimeoutError)

    upstream.release.set()
    assert await active == "answer:active"
    await client.close()


async def test_queued_cancellation_removes_waiter_and_preserves_fifo_progress() -> None:
    upstream = BlockingLLMClient()
    client = ConcurrencyLimitedLLMClient(
        upstream=upstream,
        enabled=True,
        max_concurrency=1,
        max_queue=2,
        queue_timeout_seconds=1,
    )

    active = asyncio.create_task(client.chat(_messages("active")))
    await _wait_for_started(upstream, 1)
    cancelled = asyncio.create_task(client.chat(_messages("cancelled")))
    await _wait_for_queue(client, 1)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    await _wait_for_queue(client, 0)

    successor = asyncio.create_task(client.chat(_messages("successor")))
    await _wait_for_queue(client, 1)
    upstream.release.set()
    assert await asyncio.gather(active, successor) == [
        "answer:active",
        "answer:successor",
    ]
    status = await client.status()
    assert status.metrics.cancelled == 1
    assert status.metrics.completed == 2
    await client.close()


async def test_active_cancellation_releases_capacity() -> None:
    upstream = BlockingLLMClient()
    client = ConcurrencyLimitedLLMClient(
        upstream=upstream,
        enabled=True,
        max_concurrency=1,
        max_queue=1,
        queue_timeout_seconds=1,
    )

    active = asyncio.create_task(client.chat(_messages("cancelled-active")))
    await _wait_for_started(upstream, 1)
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active

    upstream.release.set()
    assert await client.chat(_messages("next")) == "answer:next"
    status = await client.status()
    assert status.in_flight == 0
    assert status.metrics.cancelled == 1
    assert status.metrics.completed == 1
    await client.close()


async def test_upstream_failure_releases_capacity_and_is_counted() -> None:
    upstream = FailingLLMClient()
    client = ConcurrencyLimitedLLMClient(
        upstream=upstream,
        enabled=True,
        max_concurrency=1,
        max_queue=0,
    )

    with pytest.raises(ConnectionError, match="private upstream detail"):
        await client.chat(_messages("first"))
    assert await client.chat(_messages("second")) == "recovered"

    status = await client.status()
    assert status.in_flight == 0
    assert status.metrics.failed == 1
    assert status.metrics.completed == 1
    await client.close()


async def test_close_rejects_queue_drains_active_and_closes_upstream_once() -> None:
    upstream = BlockingLLMClient()
    client = ConcurrencyLimitedLLMClient(
        upstream=upstream,
        enabled=True,
        max_concurrency=1,
        max_queue=1,
        queue_timeout_seconds=1,
    )

    active = asyncio.create_task(client.chat(_messages("active")))
    await _wait_for_started(upstream, 1)
    queued = asyncio.create_task(client.chat(_messages("queued")))
    await _wait_for_queue(client, 1)
    closing = asyncio.create_task(client.close())

    with pytest.raises(ProviderLimiterClosedError):
        await queued
    await asyncio.sleep(0)
    assert closing.done() is False
    with pytest.raises(ProviderLimiterClosedError):
        await client.chat(_messages("late"))

    upstream.release.set()
    assert await active == "answer:active"
    await closing
    await client.close()
    assert upstream.closed_count == 1
    assert (await client.status()).state is ProviderLimiterStateName.CLOSED
