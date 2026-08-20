from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections.abc import Callable, Iterable
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.logging import bind_request_id, reset_request_id
from app.observability.metrics import HttpMetricsRegistry

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

DEFAULT_EXCLUDED_METRICS_PATHS = frozenset(
    {
        "/api/v1/observability/status",
        "/api/v1/security/status",
        "/health/live",
        "/health/ready",
        "/metrics",
    }
)

RequestIdFactory = Callable[[], str]


def _new_request_id() -> str:
    return f"req_{secrets.token_hex(16)}"


def select_request_id(candidate: str | None, *, factory: RequestIdFactory) -> str:
    """Accept a bounded safe correlation ID or generate a server-owned value."""

    if candidate is not None and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    generated = factory()
    if _REQUEST_ID_PATTERN.fullmatch(generated) is None:
        raise ValueError("request ID factory returned an unsafe value")
    return generated


def _route_template(scope: Scope) -> str | None:
    route = scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) else None


class RuntimeObservabilityMiddleware:
    """Correlate, measure, and safely log each HTTP request."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        metrics: HttpMetricsRegistry,
        excluded_metrics_paths: Iterable[str] = DEFAULT_EXCLUDED_METRICS_PATHS,
        request_id_factory: RequestIdFactory = _new_request_id,
        monotonic_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._app = app
        self._metrics = metrics
        self._excluded_metrics_paths = frozenset(excluded_metrics_paths)
        self._request_id_factory = request_id_factory
        self._monotonic_ns = monotonic_ns

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        candidate = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = select_request_id(candidate, factory=self._request_id_factory)
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = request_id

        path = str(scope.get("path", ""))
        measured = path not in self._excluded_metrics_paths
        method = str(scope.get("method", "OTHER"))
        started_ns = self._monotonic_ns()
        status_code = 500
        error_type: str | None = None
        if measured:
            self._metrics.request_started()
        context_token = bind_request_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        except BaseException as error:
            error_type = type(error).__name__
            status_code = 499 if isinstance(error, asyncio.CancelledError) else 500
            raise
        finally:
            duration_seconds = max(
                0.0,
                (self._monotonic_ns() - started_ns) / 1_000_000_000,
            )
            route = _route_template(scope)
            if measured:
                self._metrics.request_finished(
                    method=method,
                    route=route,
                    status_code=status_code,
                    duration_seconds=duration_seconds,
                )

            if status_code >= 500:
                log_level = logging.ERROR
                outcome = "server_error"
            elif status_code >= 400:
                log_level = logging.INFO
                outcome = "client_error"
            else:
                log_level = logging.INFO
                outcome = "success"
            extra: dict[str, Any] = {
                "request_id": request_id,
                "method": method,
                "route": route or "__unmatched__",
                "status_code": status_code,
                "duration_ms": round(duration_seconds * 1000, 3),
                "outcome": outcome,
            }
            if error_type is not None:
                extra["error_type"] = error_type
            logger.log(log_level, "http_request_completed", extra=extra)
            reset_request_id(context_token)
