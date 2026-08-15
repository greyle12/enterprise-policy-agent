from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

_ResultT = TypeVar("_ResultT")


class SingleFlightRole(StrEnum):
    """How a caller participated in one single-flight operation."""

    LEADER = "leader"
    FOLLOWER = "follower"
    OVERFLOW = "overflow"


@dataclass(frozen=True, slots=True)
class SingleFlightResult(Generic[_ResultT]):
    """Operation result plus the caller's coordination role."""

    value: _ResultT
    role: SingleFlightRole


class AsyncSingleFlight(Generic[_ResultT]):
    """Coalesce concurrent operations that use the same opaque key.

    The registry is process-local and bounded by ``max_keys``. A request for an
    existing key always joins that task. A new key that arrives while the
    registry is full executes independently instead of waiting on an unrelated
    operation.
    """

    def __init__(self, *, max_keys: int) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be at least one")
        self._max_keys = max_keys
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[_ResultT]] = {}
        self._closed = False

    @property
    def max_keys(self) -> int:
        return self._max_keys

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    async def run(
        self,
        key: str,
        operation: Callable[[], Awaitable[_ResultT]],
    ) -> SingleFlightResult[_ResultT]:
        """Run or join an operation without letting waiter cancellation spread."""

        if not key.strip():
            raise ValueError("single-flight key must not be blank")

        async with self._lock:
            if self._closed:
                raise RuntimeError("single-flight coordinator is closed")

            task = self._tasks.get(key)
            if task is not None:
                role = SingleFlightRole.FOLLOWER
            elif len(self._tasks) >= self._max_keys:
                role = SingleFlightRole.OVERFLOW
            else:
                task = asyncio.create_task(
                    self._run_and_cleanup(key, operation),
                    name="llm-singleflight",
                )
                self._tasks[key] = task
                role = SingleFlightRole.LEADER

        if role is SingleFlightRole.OVERFLOW:
            return SingleFlightResult(
                value=await operation(),
                role=role,
            )

        # A cancelled HTTP request must not cancel the shared upstream call for
        # other waiters. The task owns its own cleanup lifecycle.
        return SingleFlightResult(
            value=await asyncio.shield(task),
            role=role,
        )

    async def _run_and_cleanup(
        self,
        key: str,
        operation: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        current_task = asyncio.current_task()
        try:
            return await operation()
        finally:
            async with self._lock:
                if self._tasks.get(key) is current_task:
                    del self._tasks[key]

    async def aclose(self) -> None:
        """Reject new work and cancel any operations still active at shutdown."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = tuple(self._tasks.values())
            self._tasks.clear()

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
