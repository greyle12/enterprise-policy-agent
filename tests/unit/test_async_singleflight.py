from __future__ import annotations

import asyncio

import pytest

from app.cache import AsyncSingleFlight, SingleFlightRole


async def test_same_key_concurrent_calls_share_one_operation() -> None:
    coordinator = AsyncSingleFlight[str](max_keys=4)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "shared-answer"

    leader = asyncio.create_task(coordinator.run("same-key", operation))
    await started.wait()
    follower = asyncio.create_task(coordinator.run("same-key", operation))
    await asyncio.sleep(0)
    release.set()

    leader_result, follower_result = await asyncio.gather(leader, follower)

    assert calls == 1
    assert leader_result.value == follower_result.value == "shared-answer"
    assert leader_result.role is SingleFlightRole.LEADER
    assert follower_result.role is SingleFlightRole.FOLLOWER
    assert coordinator.in_flight == 0
    await coordinator.aclose()


async def test_cancelled_waiter_does_not_cancel_shared_operation() -> None:
    coordinator = AsyncSingleFlight[str](max_keys=4)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "survived-cancellation"

    cancelled_waiter = asyncio.create_task(coordinator.run("same-key", operation))
    await started.wait()
    surviving_waiter = asyncio.create_task(coordinator.run("same-key", operation))
    await asyncio.sleep(0)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    release.set()
    result = await surviving_waiter

    assert result.value == "survived-cancellation"
    assert result.role is SingleFlightRole.FOLLOWER
    assert calls == 1
    assert coordinator.in_flight == 0
    await coordinator.aclose()


async def test_new_key_overflows_without_joining_unrelated_operation() -> None:
    coordinator = AsyncSingleFlight[str](max_keys=1)
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first_operation() -> str:
        first_started.set()
        await release_first.wait()
        return "first"

    async def second_operation() -> str:
        return "second"

    first = asyncio.create_task(coordinator.run("first-key", first_operation))
    await first_started.wait()
    second = await coordinator.run("second-key", second_operation)

    assert second.value == "second"
    assert second.role is SingleFlightRole.OVERFLOW
    assert coordinator.in_flight == 1

    release_first.set()
    first_result = await first
    assert first_result.role is SingleFlightRole.LEADER
    await coordinator.aclose()


async def test_failed_operation_is_removed_and_can_be_retried() -> None:
    coordinator = AsyncSingleFlight[str](max_keys=4)
    first_started = asyncio.Event()
    release_failure = asyncio.Event()
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            first_started.set()
            await release_failure.wait()
            raise RuntimeError("temporary model failure")
        return "recovered"

    first = asyncio.create_task(coordinator.run("retry-key", operation))
    await first_started.wait()
    follower = asyncio.create_task(coordinator.run("retry-key", operation))
    await asyncio.sleep(0)
    release_failure.set()

    results = await asyncio.gather(first, follower, return_exceptions=True)

    assert attempts == 1
    assert all(isinstance(result, RuntimeError) for result in results)
    assert coordinator.in_flight == 0

    recovered = await coordinator.run("retry-key", operation)
    assert recovered.value == "recovered"
    assert recovered.role is SingleFlightRole.LEADER
    assert attempts == 2
    await coordinator.aclose()


async def test_close_cancels_active_operation_and_rejects_new_work() -> None:
    coordinator = AsyncSingleFlight[str](max_keys=4)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def operation() -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return "unreachable"

    request = asyncio.create_task(coordinator.run("active-key", operation))
    await started.wait()

    await coordinator.aclose()

    with pytest.raises(asyncio.CancelledError):
        await request
    assert cancelled.is_set()
    assert coordinator.in_flight == 0
    with pytest.raises(RuntimeError, match="closed"):
        await coordinator.run("new-key", operation)
