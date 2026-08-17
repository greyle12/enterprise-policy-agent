from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.llm.client import ChatMessage, LLMClient


class ClosableLLMClient(LLMClient, Protocol):
    """LLM client that owns an async transport."""

    async def close(self) -> None: ...


class ProviderCapacityError(RuntimeError):
    """Base error for safe, retryable local provider-capacity failures."""

    status_code = 503
    code = "llm_provider_unavailable"
    user_message = "LLM provider capacity is temporarily unavailable; retry later."

    def __init__(self) -> None:
        super().__init__(self.user_message)


class ProviderOverloadedError(ProviderCapacityError):
    """Raised immediately when the bounded waiting queue is full."""

    code = "llm_provider_overloaded"
    user_message = "LLM provider is busy and its waiting queue is full; retry later."


class ProviderQueueTimeoutError(ProviderCapacityError, TimeoutError):
    """Raised when an admitted request waits too long for provider capacity."""

    code = "llm_provider_queue_timeout"
    user_message = "LLM provider capacity was not available before the queue timeout."


class ProviderLimiterClosedError(ProviderCapacityError):
    """Raised when work reaches a limiter that is shutting down."""

    code = "llm_provider_limiter_closed"
    user_message = "LLM provider capacity manager is shutting down; retry later."


class ProviderLimiterStateName(StrEnum):
    """Operator-facing state of the process-local provider limiter."""

    DISABLED = "disabled"
    AVAILABLE = "available"
    QUEUING = "queuing"
    SATURATED = "saturated"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ProviderLimiterMetricsSnapshot:
    """Secret-free process-local counters since application startup."""

    requests: int
    bypassed: int
    accepted: int
    started: int
    completed: int
    failed: int
    rejected: int
    timed_out: int
    cancelled: int
    peak_in_flight: int
    peak_queued: int
    average_wait_ms: float


@dataclass(frozen=True, slots=True)
class ProviderLimiterStatus:
    """Safe status snapshot containing no prompts or provider credentials."""

    enabled: bool
    state: ProviderLimiterStateName
    max_concurrency: int
    max_queue: int
    queue_timeout_seconds: float
    in_flight: int
    queued: int
    metrics: ProviderLimiterMetricsSnapshot


@dataclass(slots=True)
class _MutableProviderLimiterMetrics:
    requests: int = 0
    bypassed: int = 0
    accepted: int = 0
    started: int = 0
    completed: int = 0
    failed: int = 0
    rejected: int = 0
    timed_out: int = 0
    cancelled: int = 0
    peak_in_flight: int = 0
    peak_queued: int = 0
    total_wait_ms: float = 0.0

    def snapshot(self) -> ProviderLimiterMetricsSnapshot:
        average_wait_ms = self.total_wait_ms / self.started if self.started else 0.0
        return ProviderLimiterMetricsSnapshot(
            requests=self.requests,
            bypassed=self.bypassed,
            accepted=self.accepted,
            started=self.started,
            completed=self.completed,
            failed=self.failed,
            rejected=self.rejected,
            timed_out=self.timed_out,
            cancelled=self.cancelled,
            peak_in_flight=self.peak_in_flight,
            peak_queued=self.peak_queued,
            average_wait_ms=round(average_wait_ms, 3),
        )


class ConcurrencyLimitedLLMClient:
    """Bound provider calls with a FIFO queue, timeout, and safe metrics."""

    def __init__(
        self,
        *,
        upstream: ClosableLLMClient,
        enabled: bool = False,
        max_concurrency: int = 4,
        max_queue: int = 16,
        queue_timeout_seconds: float = 2.0,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")
        if max_queue < 0:
            raise ValueError("max_queue must not be negative")
        if queue_timeout_seconds <= 0:
            raise ValueError("queue_timeout_seconds must be greater than zero")

        self._upstream = upstream
        self._enabled = enabled
        self._max_concurrency = max_concurrency
        self._max_queue = max_queue
        self._queue_timeout_seconds = queue_timeout_seconds
        self._condition = asyncio.Condition()
        self._close_lock = asyncio.Lock()
        self._waiters: deque[object] = deque()
        self._in_flight = 0
        self._closed = False
        self._upstream_closed = False
        self._metrics = _MutableProviderLimiterMetrics()

    def _start_call(self, *, wait_ms: float) -> None:
        self._in_flight += 1
        self._metrics.started += 1
        self._metrics.total_wait_ms += wait_ms
        self._metrics.peak_in_flight = max(
            self._metrics.peak_in_flight,
            self._in_flight,
        )

    def _remove_waiter(self, waiter: object) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            return

    async def _acquire(self) -> None:
        started_ns = time.perf_counter_ns()
        async with self._condition:
            self._metrics.requests += 1
            if self._closed:
                self._metrics.rejected += 1
                raise ProviderLimiterClosedError

            if not self._enabled:
                self._metrics.bypassed += 1
                self._metrics.accepted += 1
                self._start_call(wait_ms=0.0)
                return

            if self._in_flight < self._max_concurrency and not self._waiters:
                self._metrics.accepted += 1
                self._start_call(wait_ms=0.0)
                return

            if len(self._waiters) >= self._max_queue:
                self._metrics.rejected += 1
                raise ProviderOverloadedError

            waiter = object()
            self._waiters.append(waiter)
            self._metrics.accepted += 1
            self._metrics.peak_queued = max(
                self._metrics.peak_queued,
                len(self._waiters),
            )

            def can_start() -> bool:
                return self._closed or (
                    bool(self._waiters)
                    and self._waiters[0] is waiter
                    and self._in_flight < self._max_concurrency
                )

            try:
                async with asyncio.timeout(self._queue_timeout_seconds):
                    await self._condition.wait_for(can_start)
            except TimeoutError:
                self._remove_waiter(waiter)
                self._metrics.timed_out += 1
                self._condition.notify_all()
                raise ProviderQueueTimeoutError from None
            except asyncio.CancelledError:
                self._remove_waiter(waiter)
                self._metrics.cancelled += 1
                self._condition.notify_all()
                raise

            if self._closed:
                self._remove_waiter(waiter)
                self._metrics.cancelled += 1
                self._condition.notify_all()
                raise ProviderLimiterClosedError

            popped = self._waiters.popleft()
            if popped is not waiter:
                raise RuntimeError("provider limiter FIFO invariant violated")
            wait_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
            self._start_call(wait_ms=wait_ms)
            self._condition.notify_all()

    async def _finish(self, *, outcome: str) -> None:
        async with self._condition:
            if self._in_flight < 1:
                raise RuntimeError("provider limiter in-flight counter underflow")
            self._in_flight -= 1
            if outcome == "completed":
                self._metrics.completed += 1
            elif outcome == "cancelled":
                self._metrics.cancelled += 1
            else:
                self._metrics.failed += 1
            self._condition.notify_all()

    async def chat(self, messages: Sequence[ChatMessage]) -> str:
        """Wait for bounded capacity, call upstream, and always release the permit."""

        await self._acquire()
        outcome = "failed"
        try:
            response = await self._upstream.chat(messages)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        else:
            outcome = "completed"
            return response
        finally:
            await self._finish(outcome=outcome)

    async def status(self) -> ProviderLimiterStatus:
        """Return a consistent, prompt-free status snapshot."""

        async with self._condition:
            queued = len(self._waiters)
            if self._closed:
                state = ProviderLimiterStateName.CLOSED
            elif not self._enabled:
                state = ProviderLimiterStateName.DISABLED
            elif (
                self._in_flight >= self._max_concurrency
                and queued >= self._max_queue
            ):
                state = ProviderLimiterStateName.SATURATED
            elif queued:
                state = ProviderLimiterStateName.QUEUING
            else:
                state = ProviderLimiterStateName.AVAILABLE

            return ProviderLimiterStatus(
                enabled=self._enabled,
                state=state,
                max_concurrency=self._max_concurrency,
                max_queue=self._max_queue,
                queue_timeout_seconds=self._queue_timeout_seconds,
                in_flight=self._in_flight,
                queued=queued,
                metrics=self._metrics.snapshot(),
            )

    async def close(self) -> None:
        """Reject queued/new work, drain active calls, then close the upstream once."""

        async with self._close_lock:
            if self._upstream_closed:
                return
            async with self._condition:
                self._closed = True
                self._condition.notify_all()
                await self._condition.wait_for(
                    lambda: self._in_flight == 0 and not self._waiters
                )
            await self._upstream.close()
            self._upstream_closed = True
