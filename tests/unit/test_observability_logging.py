from __future__ import annotations

import json
import logging

import pytest

from app.observability import JsonLogFormatter, build_json_logging_config
from app.observability.logging import bind_request_id, reset_request_id


def _record(**extra: object) -> logging.LogRecord:
    return logging.Logger("test.observability").makeRecord(
        name="test.observability",
        level=logging.INFO,
        fn=__file__,
        lno=1,
        msg="http_request_completed",
        args=(),
        exc_info=None,
        extra=extra,
    )


def test_json_formatter_emits_only_safe_access_fields() -> None:
    formatted = JsonLogFormatter().format(
        _record(
            request_id="request-123",
            method="GET",
            route="/policy-answers",
            status_code=200,
            duration_ms=12.5,
            outcome="success",
            raw_query="api_key=do-not-log",
            request_body="confidential question",
        )
    )
    payload = json.loads(formatted)

    assert payload["event"] == "http_request_completed"
    assert payload["request_id"] == "request-123"
    assert payload["route"] == "/policy-answers"
    assert payload["duration_ms"] == 12.5
    assert "raw_query" not in payload
    assert "request_body" not in payload
    assert "do-not-log" not in formatted
    assert "confidential question" not in formatted


def test_json_formatter_uses_context_and_omits_exception_message() -> None:
    token = bind_request_id("context-request-456")
    try:
        record = _record(error_type="RuntimeError")
        record.exc_info = (
            RuntimeError,
            RuntimeError("api_key=private-exception-detail"),
            None,
        )
        formatted = JsonLogFormatter().format(record)
    finally:
        reset_request_id(token)

    payload = json.loads(formatted)
    assert payload["request_id"] == "context-request-456"
    assert payload["exception_type"] == "RuntimeError"
    assert "private-exception-detail" not in formatted


def test_logging_config_disables_raw_uvicorn_access_log() -> None:
    config = build_json_logging_config("info")

    assert config["root"]["level"] == "INFO"
    assert config["loggers"]["uvicorn.access"]["handlers"] == []
    assert config["formatters"]["json"]["()"] == ("app.observability.logging.JsonLogFormatter")


def test_logging_config_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="unsupported log level"):
        build_json_logging_config("verbose")
