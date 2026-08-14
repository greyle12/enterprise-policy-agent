from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar
from uuid import uuid4

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.resilience.models import (
    ToolCallOutcome,
    ToolCallRecord,
    ToolErrorInfo,
    ToolFailureCategory,
    ToolName,
    ToolOperationKind,
    ToolRecoveryAction,
)

T = TypeVar("T")

_RETRYABLE_CATEGORIES = {
    ToolFailureCategory.TIMEOUT,
    ToolFailureCategory.RATE_LIMITED,
    ToolFailureCategory.UPSTREAM_UNAVAILABLE,
}

_ERROR_CODE_BY_CATEGORY = {
    ToolFailureCategory.TIMEOUT: "tool_timeout",
    ToolFailureCategory.RATE_LIMITED: "tool_rate_limited",
    ToolFailureCategory.UPSTREAM_UNAVAILABLE: "tool_upstream_unavailable",
    ToolFailureCategory.INVALID_RESPONSE: "tool_invalid_response",
    ToolFailureCategory.INTERNAL_ERROR: "tool_internal_error",
}

_TOOL_LABELS = {
    ToolName.INTENT_CLASSIFIER: "意图识别",
    ToolName.POLICY_ANSWER: "制度问答",
    ToolName.MATERIAL_CHECK: "材料检查",
    ToolName.APPROVAL_CHECK: "审批判断",
    ToolName.DRAFT_GENERATION: "草稿生成",
    ToolName.DRAFT_REVISION: "草稿修改",
    ToolName.APPROVAL_SUBMISSION: "审批提交",
    ToolName.POLICY_RESEARCH: "内部制度研究",
    ToolName.WEB_SEARCH: "外部网页搜索",
}


@dataclass(frozen=True, slots=True)
class _ToolExecutionPolicy:
    timeout_seconds: float
    max_attempts: int
    retry_safe: bool


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome(Generic[T]):
    """成功工具调用的值和可观测记录。"""

    value: T
    record: ToolCallRecord


class ToolExecutionError(RuntimeError):
    """工具调用失败；只把已经脱敏的记录暴露给工作流。"""

    def __init__(self, record: ToolCallRecord) -> None:
        error = record.error
        if error is None:
            raise ValueError("failed tool record must include error details")
        super().__init__(error.user_message)
        self.record = record


def _status_code_from(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def classify_tool_failure(exc: Exception) -> ToolFailureCategory:
    """把第三方异常归一化，避免工作流依赖供应商异常正文。"""

    if isinstance(
        exc,
        (TimeoutError, httpx.TimeoutException, APITimeoutError),
    ):
        return ToolFailureCategory.TIMEOUT

    status_code = _status_code_from(exc)
    if isinstance(exc, RateLimitError) or status_code == 429:
        return ToolFailureCategory.RATE_LIMITED

    if isinstance(
        exc,
        (
            ConnectionError,
            OSError,
            httpx.NetworkError,
            APIConnectionError,
        ),
    ):
        return ToolFailureCategory.UPSTREAM_UNAVAILABLE

    if status_code is not None:
        if status_code >= 500:
            return ToolFailureCategory.UPSTREAM_UNAVAILABLE
        return ToolFailureCategory.INVALID_RESPONSE

    if isinstance(
        exc,
        (
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValidationError,
            ValueError,
            TypeError,
            RuntimeError,
        ),
    ):
        return ToolFailureCategory.INVALID_RESPONSE

    return ToolFailureCategory.INTERNAL_ERROR


def _user_message(
    *,
    tool: ToolName,
    category: ToolFailureCategory,
) -> str:
    if tool is ToolName.APPROVAL_SUBMISSION:
        return (
            "审批提交结果暂时无法确认，系统没有自动重复提交。"
            "请保留当前会话并稍后再次回复“提交审批”；"
            "相同幂等键会防止重复创建审批申请。"
        )

    label = _TOOL_LABELS[tool]
    if category is ToolFailureCategory.INVALID_RESPONSE:
        return f"{label}返回了不可信的结果，本轮没有采用该结果。请联系系统管理员检查工具输出。"
    if category is ToolFailureCategory.INTERNAL_ERROR:
        return f"{label}发生内部错误，本轮已安全停止。请联系系统管理员。"
    return f"{label}服务暂时不可用，本轮已安全停止。请稍后重试。"


class ResilientToolExecutor:
    """使用 Tenacity 为 Agent 工具提供有界重试、超时和安全错误。"""

    def __init__(
        self,
        *,
        safe_tool_timeout_seconds: float = 65.0,
        mutation_tool_timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        retry_min_wait_seconds: float = 0.1,
        retry_max_wait_seconds: float = 1.0,
        error_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if safe_tool_timeout_seconds <= 0:
            raise ValueError("safe_tool_timeout_seconds must be greater than zero")
        if mutation_tool_timeout_seconds <= 0:
            raise ValueError("mutation_tool_timeout_seconds must be greater than zero")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if retry_min_wait_seconds < 0:
            raise ValueError("retry_min_wait_seconds must not be negative")
        if retry_max_wait_seconds < retry_min_wait_seconds:
            raise ValueError(
                "retry_max_wait_seconds must be greater than or equal to retry_min_wait_seconds"
            )

        self._safe_tool_timeout_seconds = safe_tool_timeout_seconds
        self._mutation_tool_timeout_seconds = mutation_tool_timeout_seconds
        self._max_attempts = max_attempts
        self._retry_min_wait_seconds = retry_min_wait_seconds
        self._retry_max_wait_seconds = retry_max_wait_seconds
        self._error_id_factory = error_id_factory or (lambda: f"ERR-{uuid4().hex[:12].upper()}")

    def _policy_for(self, operation: ToolOperationKind) -> _ToolExecutionPolicy:
        if operation is ToolOperationKind.MUTATION:
            return _ToolExecutionPolicy(
                timeout_seconds=self._mutation_tool_timeout_seconds,
                max_attempts=1,
                retry_safe=False,
            )
        return _ToolExecutionPolicy(
            timeout_seconds=self._safe_tool_timeout_seconds,
            max_attempts=self._max_attempts,
            retry_safe=True,
        )

    @staticmethod
    def _is_retryable(
        exc: Exception,
        *,
        policy: _ToolExecutionPolicy,
        passthrough_exceptions: tuple[type[Exception], ...],
    ) -> bool:
        if passthrough_exceptions and isinstance(exc, passthrough_exceptions):
            return False
        return policy.retry_safe and classify_tool_failure(exc) in _RETRYABLE_CATEGORIES

    async def execute(
        self,
        *,
        tool: ToolName,
        operation: ToolOperationKind,
        call: Callable[[], Awaitable[T]],
        passthrough_exceptions: tuple[type[Exception], ...] = (),
    ) -> ToolExecutionOutcome[T]:
        """执行一次逻辑工具调用；仅为重试安全的瞬时错误自动重试。"""

        policy = self._policy_for(operation)
        attempts = 0
        retrying = AsyncRetrying(
            stop=stop_after_attempt(policy.max_attempts),
            wait=wait_exponential(
                multiplier=self._retry_min_wait_seconds,
                min=self._retry_min_wait_seconds,
                max=self._retry_max_wait_seconds,
            ),
            retry=retry_if_exception(
                lambda exc: self._is_retryable(
                    exc,
                    policy=policy,
                    passthrough_exceptions=passthrough_exceptions,
                )
            ),
            reraise=True,
        )

        try:
            async for attempt in retrying:
                with attempt:
                    attempts += 1
                    value = await asyncio.wait_for(
                        call(),
                        timeout=policy.timeout_seconds,
                    )
        except Exception as exc:
            if passthrough_exceptions and isinstance(exc, passthrough_exceptions):
                raise
            category = classify_tool_failure(exc)
            retryable = category in _RETRYABLE_CATEGORIES
            recovery_action = (
                ToolRecoveryAction.RESUBMIT_WITH_SAME_SESSION
                if tool is ToolName.APPROVAL_SUBMISSION
                else (
                    ToolRecoveryAction.RETRY_LATER
                    if retryable
                    else ToolRecoveryAction.CONTACT_SUPPORT
                )
            )
            error = ToolErrorInfo(
                error_id=self._error_id_factory(),
                code=_ERROR_CODE_BY_CATEGORY[category],
                category=category,
                retryable=retryable,
                recovery_action=recovery_action,
                user_message=_user_message(tool=tool, category=category),
            )
            raise ToolExecutionError(
                ToolCallRecord(
                    tool=tool,
                    operation=operation,
                    outcome=ToolCallOutcome.FAILED,
                    attempts=max(attempts, 1),
                    max_attempts=policy.max_attempts,
                    timeout_seconds=policy.timeout_seconds,
                    retry_safe=policy.retry_safe,
                    error=error,
                )
            ) from exc

        return ToolExecutionOutcome(
            value=value,
            record=ToolCallRecord(
                tool=tool,
                operation=operation,
                outcome=(ToolCallOutcome.RECOVERED if attempts > 1 else ToolCallOutcome.SUCCESS),
                attempts=attempts,
                max_attempts=policy.max_attempts,
                timeout_seconds=policy.timeout_seconds,
                retry_safe=policy.retry_safe,
            ),
        )
