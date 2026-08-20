from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_LOG_LEVELS = frozenset({"CRITICAL", "DEBUG", "ERROR", "INFO", "WARNING"})
_REQUEST_ID: ContextVar[str | None] = ContextVar("http_request_id", default=None)

_SAFE_EXTRA_FIELDS = (
    "method",
    "route",
    "status_code",
    "duration_ms",
    "outcome",
    "error_type",
)


def bind_request_id(request_id: str) -> Token[str | None]:
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _REQUEST_ID.reset(token)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def _safe_request_id(value: object) -> str | None:
    if isinstance(value, str) and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return None


class JsonLogFormatter(logging.Formatter):
    """One-line JSON formatter that omits exception messages and tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(
            timespec="milliseconds"
        )
        payload: dict[str, Any] = {
            "timestamp": timestamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        request_id = _safe_request_id(getattr(record, "request_id", None) or current_request_id())
        if request_id is not None:
            payload["request_id"] = request_id

        for field_name in _SAFE_EXTRA_FIELDS:
            value = getattr(record, field_name, None)
            if isinstance(value, str | int | float | bool):
                payload[field_name] = value

        if record.exc_info is not None and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def build_json_logging_config(level: str) -> dict[str, Any]:
    """Build a Uvicorn-compatible JSON logging configuration."""

    normalized_level = level.strip().upper()
    if normalized_level not in _LOG_LEVELS:
        raise ValueError("unsupported log level")
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "app.observability.logging.JsonLogFormatter",
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {
            "handlers": ["default"],
            "level": normalized_level,
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["default"],
                "level": normalized_level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": [],
                "level": normalized_level,
                "propagate": False,
            },
        },
    }
