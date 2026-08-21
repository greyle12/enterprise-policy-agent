from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.llm import (
    ConcurrencyLimitedLLMClient,
    ProviderLimiterStateName,
    ProviderOverloadedError,
    ProviderQueueTimeoutError,
)
from app.llm.client import ChatMessage


@dataclass
class _BlockingProvider:
    started: list[str] = field(default_factory=list)
    active: int = 0
    peak_active: int = 0
    closed: bool = False
    release: asyncio.Event = field(default_factory=asyncio.Event)

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
        self.closed = True


def _messages(label: str) -> tuple[ChatMessage, ...]:
    return ({"role": "user", "content": label},)


async def _wait_for_started(provider: _BlockingProvider, count: int) -> None:
    async with asyncio.timeout(1):
        while len(provider.started) < count:
            await asyncio.sleep(0)


async def _wait_for_queued(client: ConcurrencyLimitedLLMClient, count: int) -> None:
    async with asyncio.timeout(1):
        while (await client.status()).queued != count:
            await asyncio.sleep(0)


async def _run_verification() -> dict[str, object]:
    capacity_provider = _BlockingProvider()
    capacity_client = ConcurrencyLimitedLLMClient(
        upstream=capacity_provider,
        enabled=True,
        max_concurrency=2,
        max_queue=2,
        queue_timeout_seconds=1,
    )
    capacity_tasks = [
        asyncio.create_task(capacity_client.chat(_messages(label)))
        for label in ("active-1", "active-2")
    ]
    await _wait_for_started(capacity_provider, 2)
    capacity_tasks.extend(
        asyncio.create_task(capacity_client.chat(_messages(label)))
        for label in ("queued-1", "queued-2")
    )
    await _wait_for_queued(capacity_client, 2)
    saturated_status = await capacity_client.status()
    try:
        await capacity_client.chat(_messages("overflow"))
    except ProviderOverloadedError:
        overflow_rejected = True
    else:
        overflow_rejected = False
    capacity_provider.release.set()
    capacity_answers = await asyncio.gather(*capacity_tasks)
    capacity_status = await capacity_client.status()

    timeout_provider = _BlockingProvider()
    timeout_client = ConcurrencyLimitedLLMClient(
        upstream=timeout_provider,
        enabled=True,
        max_concurrency=1,
        max_queue=1,
        queue_timeout_seconds=0.01,
    )
    timeout_active = asyncio.create_task(timeout_client.chat(_messages("active")))
    await _wait_for_started(timeout_provider, 1)
    try:
        await timeout_client.chat(_messages("timeout"))
    except ProviderQueueTimeoutError:
        timeout_observed = True
    else:
        timeout_observed = False
    timeout_provider.release.set()
    await timeout_active
    timeout_status = await timeout_client.status()

    cancellation_provider = _BlockingProvider()
    cancellation_client = ConcurrencyLimitedLLMClient(
        upstream=cancellation_provider,
        enabled=True,
        max_concurrency=1,
        max_queue=1,
        queue_timeout_seconds=1,
    )
    cancellation_active = asyncio.create_task(cancellation_client.chat(_messages("active")))
    await _wait_for_started(cancellation_provider, 1)
    cancelled = asyncio.create_task(cancellation_client.chat(_messages("cancelled")))
    await _wait_for_queued(cancellation_client, 1)
    cancelled.cancel()
    try:
        await cancelled
    except asyncio.CancelledError:
        cancellation_observed = True
    else:
        cancellation_observed = False
    cancellation_provider.release.set()
    await cancellation_active
    cancellation_status = await cancellation_client.status()

    disabled_provider = _BlockingProvider()
    disabled_client = ConcurrencyLimitedLLMClient(
        upstream=disabled_provider,
        enabled=False,
        max_concurrency=1,
        max_queue=0,
    )
    disabled_tasks = [
        asyncio.create_task(disabled_client.chat(_messages(label)))
        for label in ("disabled-1", "disabled-2")
    ]
    await _wait_for_started(disabled_provider, 2)
    disabled_active_status = await disabled_client.status()
    disabled_provider.release.set()
    await asyncio.gather(*disabled_tasks)
    disabled_status = await disabled_client.status()

    checks = {
        "active_calls_are_bounded": (
            capacity_provider.peak_active == 2 and capacity_status.metrics.peak_in_flight == 2
        ),
        "fifo_queue_is_bounded": (
            saturated_status.state is ProviderLimiterStateName.SATURATED
            and saturated_status.queued == 2
            and capacity_provider.started == ["active-1", "active-2", "queued-1", "queued-2"]
        ),
        "overflow_is_rejected": (overflow_rejected and capacity_status.metrics.rejected == 1),
        "admitted_work_completes": (
            capacity_answers
            == [
                "answer:active-1",
                "answer:active-2",
                "answer:queued-1",
                "answer:queued-2",
            ]
            and capacity_status.metrics.completed == 4
        ),
        "queue_timeout_cleans_up": (
            timeout_observed
            and timeout_status.queued == 0
            and timeout_status.in_flight == 0
            and timeout_status.metrics.timed_out == 1
        ),
        "queued_cancellation_cleans_up": (
            cancellation_observed
            and cancellation_status.queued == 0
            and cancellation_status.in_flight == 0
            and cancellation_status.metrics.cancelled == 1
        ),
        "disabled_mode_preserves_compatibility": (
            disabled_active_status.state is ProviderLimiterStateName.DISABLED
            and disabled_active_status.in_flight == 2
            and disabled_provider.peak_active == 2
            and disabled_status.metrics.bypassed == 2
        ),
    }

    await capacity_client.close()
    await timeout_client.close()
    await cancellation_client.close()
    await disabled_client.close()
    checks["resources_closed"] = all(
        provider.closed
        for provider in (
            capacity_provider,
            timeout_provider,
            cancellation_provider,
            disabled_provider,
        )
    )

    return {
        "passed": all(checks.values()),
        "configuration": {
            "max_concurrency": 2,
            "max_queue": 2,
            "queue_timeout_seconds": 1,
        },
        "capacity_metrics": {
            "requests": capacity_status.metrics.requests,
            "accepted": capacity_status.metrics.accepted,
            "completed": capacity_status.metrics.completed,
            "rejected": capacity_status.metrics.rejected,
            "peak_in_flight": capacity_status.metrics.peak_in_flight,
            "peak_queued": capacity_status.metrics.peak_queued,
        },
        "checks": checks,
        "network_calls": False,
        "live_llm_calls": False,
    }


def run_verification() -> dict[str, object]:
    """Exercise the Day 27 provider backpressure contract entirely offline."""

    return asyncio.run(_run_verification())


def main() -> int:
    report = run_verification()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
