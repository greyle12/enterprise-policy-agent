from __future__ import annotations

import asyncio

import httpx
import pytest

from app.llm import ProviderOverloadedError, ProviderQueueTimeoutError
from app.resilience import (
    ResilientToolExecutor,
    ToolCallOutcome,
    ToolExecutionError,
    ToolFailureCategory,
    ToolName,
    ToolOperationKind,
    ToolRecoveryAction,
)


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ProviderQueueTimeoutError(), ToolFailureCategory.TIMEOUT),
        (ProviderOverloadedError(), ToolFailureCategory.UPSTREAM_UNAVAILABLE),
    ],
)
async def test_provider_capacity_errors_are_retryable_and_sanitized(
    error: Exception,
    category: ToolFailureCategory,
) -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(ToolExecutionError) as captured:
        await _executor(max_attempts=2).execute(
            tool=ToolName.POLICY_ANSWER,
            operation=ToolOperationKind.READ_ONLY,
            call=operation,
        )

    record = captured.value.record
    assert calls == 2
    assert record.error is not None
    assert record.error.category is category
    assert record.error.retryable is True
    assert "provider" not in record.error.user_message.lower()


def _executor(**overrides) -> ResilientToolExecutor:
    values = {
        "safe_tool_timeout_seconds": 0.1,
        "mutation_tool_timeout_seconds": 0.1,
        "max_attempts": 3,
        "retry_min_wait_seconds": 0.0,
        "retry_max_wait_seconds": 0.0,
        "error_id_factory": lambda: "ERR-TEST00000001",
    }
    values.update(overrides)
    return ResilientToolExecutor(**values)


@pytest.mark.asyncio
async def test_returns_success_record_without_retry() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await _executor().execute(
        tool=ToolName.POLICY_ANSWER,
        operation=ToolOperationKind.READ_ONLY,
        call=operation,
    )

    assert result.value == "ok"
    assert calls == 1
    assert result.record.outcome is ToolCallOutcome.SUCCESS
    assert result.record.attempts == 1
    assert result.record.error is None


@pytest.mark.asyncio
async def test_retries_transient_failure_and_reports_recovery() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("temporary upstream failure")
        return "recovered"

    result = await _executor().execute(
        tool=ToolName.MATERIAL_CHECK,
        operation=ToolOperationKind.READ_ONLY,
        call=operation,
    )

    assert result.value == "recovered"
    assert calls == 3
    assert result.record.outcome is ToolCallOutcome.RECOVERED
    assert result.record.attempts == 3
    assert result.record.retry_safe is True


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_sanitized() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "too late"

    executor = _executor(
        safe_tool_timeout_seconds=0.001,
        max_attempts=2,
    )

    with pytest.raises(ToolExecutionError) as captured:
        await executor.execute(
            tool=ToolName.POLICY_ANSWER,
            operation=ToolOperationKind.READ_ONLY,
            call=operation,
        )

    record = captured.value.record
    assert calls == 2
    assert record.attempts == 2
    assert record.outcome is ToolCallOutcome.FAILED
    assert record.error is not None
    assert record.error.category is ToolFailureCategory.TIMEOUT
    assert record.error.code == "tool_timeout"
    assert record.error.error_id == "ERR-TEST00000001"


@pytest.mark.asyncio
async def test_invalid_response_fails_fast_without_retry_or_leak() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("api_key=very-secret-value")

    with pytest.raises(ToolExecutionError) as captured:
        await _executor().execute(
            tool=ToolName.APPROVAL_CHECK,
            operation=ToolOperationKind.READ_ONLY,
            call=operation,
        )

    record = captured.value.record
    assert calls == 1
    assert record.error is not None
    assert record.error.category is ToolFailureCategory.INVALID_RESPONSE
    assert record.error.retryable is False
    assert record.error.recovery_action is ToolRecoveryAction.CONTACT_SUPPORT
    assert "系统管理员" in record.error.user_message
    assert "very-secret-value" not in str(captured.value)
    assert "very-secret-value" not in record.error.user_message


@pytest.mark.asyncio
async def test_rate_limit_is_classified_and_retried() -> None:
    class FakeRateLimitError(Exception):
        status_code = 429

    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise FakeRateLimitError("rate limit response body")

    with pytest.raises(ToolExecutionError) as captured:
        await _executor(max_attempts=2).execute(
            tool=ToolName.INTENT_CLASSIFIER,
            operation=ToolOperationKind.READ_ONLY,
            call=operation,
        )

    record = captured.value.record
    assert calls == 2
    assert record.error is not None
    assert record.error.category is ToolFailureCategory.RATE_LIMITED
    assert record.error.code == "tool_rate_limited"
    assert record.error.retryable is True


@pytest.mark.asyncio
async def test_httpx_server_error_is_classified_and_retried() -> None:
    calls = 0
    request = httpx.Request("POST", "https://example.test/search")
    response = httpx.Response(503, request=request)

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise httpx.HTTPStatusError(
            "provider details",
            request=request,
            response=response,
        )

    with pytest.raises(ToolExecutionError) as captured:
        await _executor(max_attempts=2).execute(
            tool=ToolName.WEB_SEARCH,
            operation=ToolOperationKind.READ_ONLY,
            call=operation,
        )

    record = captured.value.record
    assert calls == 2
    assert record.error is not None
    assert record.error.category is ToolFailureCategory.UPSTREAM_UNAVAILABLE
    assert record.error.retryable is True


@pytest.mark.asyncio
async def test_httpx_client_error_fails_fast_as_invalid_response() -> None:
    calls = 0
    request = httpx.Request("POST", "https://example.test/search")
    response = httpx.Response(400, request=request)

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise httpx.HTTPStatusError(
            "provider details",
            request=request,
            response=response,
        )

    with pytest.raises(ToolExecutionError) as captured:
        await _executor().execute(
            tool=ToolName.WEB_SEARCH,
            operation=ToolOperationKind.READ_ONLY,
            call=operation,
        )

    record = captured.value.record
    assert calls == 1
    assert record.error is not None
    assert record.error.category is ToolFailureCategory.INVALID_RESPONSE
    assert record.error.retryable is False


@pytest.mark.asyncio
async def test_mutation_is_never_automatically_retried() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ConnectionError("submission connection lost")

    with pytest.raises(ToolExecutionError) as captured:
        await _executor(max_attempts=5).execute(
            tool=ToolName.APPROVAL_SUBMISSION,
            operation=ToolOperationKind.MUTATION,
            call=operation,
        )

    record = captured.value.record
    assert calls == 1
    assert record.max_attempts == 1
    assert record.retry_safe is False
    assert record.error is not None
    assert record.error.retryable is True
    assert record.error.recovery_action is (ToolRecoveryAction.RESUBMIT_WITH_SAME_SESSION)


@pytest.mark.asyncio
async def test_passthrough_business_error_is_not_wrapped() -> None:
    class BusinessRuleError(ValueError):
        pass

    async def operation() -> str:
        raise BusinessRuleError("expected business validation")

    with pytest.raises(BusinessRuleError):
        await _executor().execute(
            tool=ToolName.APPROVAL_SUBMISSION,
            operation=ToolOperationKind.MUTATION,
            call=operation,
            passthrough_exceptions=(BusinessRuleError,),
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"safe_tool_timeout_seconds": 0}, "safe_tool_timeout_seconds"),
        ({"mutation_tool_timeout_seconds": 0}, "mutation_tool_timeout_seconds"),
        ({"max_attempts": 0}, "max_attempts"),
        ({"retry_min_wait_seconds": -1}, "retry_min_wait_seconds"),
        (
            {
                "retry_min_wait_seconds": 2,
                "retry_max_wait_seconds": 1,
            },
            "retry_max_wait_seconds",
        ),
    ],
)
def test_rejects_invalid_executor_configuration(overrides, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _executor(**overrides)
