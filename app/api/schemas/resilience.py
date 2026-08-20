from __future__ import annotations

from pydantic import BaseModel

from app.resilience import (
    AgentResilienceInfo,
    ToolCallOutcome,
    ToolFailureCategory,
    ToolName,
    ToolOperationKind,
    ToolRecoveryAction,
)


class ToolErrorResponse(BaseModel):
    """不含异常正文、请求内容和凭据的安全错误。"""

    error_id: str
    code: str
    category: ToolFailureCategory
    retryable: bool
    recovery_action: ToolRecoveryAction
    message: str


class ToolCallResponse(BaseModel):
    """一个受保护工具的最终执行记录。"""

    tool: ToolName
    operation: ToolOperationKind
    outcome: ToolCallOutcome
    attempts: int
    max_attempts: int
    timeout_seconds: float
    retry_safe: bool
    error: ToolErrorResponse | None = None


class AgentResilienceResponse(BaseModel):
    """调用方判断重试恢复或安全降级所需的摘要。"""

    degraded: bool
    recovered: bool
    tool_calls: list[ToolCallResponse]


def build_resilience_response(
    info: AgentResilienceInfo | None,
) -> AgentResilienceResponse | None:
    """把领域容错记录转换为统一 API 模型。"""

    if info is None:
        return None
    return AgentResilienceResponse(
        degraded=info.degraded,
        recovered=info.recovered,
        tool_calls=[
            ToolCallResponse(
                tool=record.tool,
                operation=record.operation,
                outcome=record.outcome,
                attempts=record.attempts,
                max_attempts=record.max_attempts,
                timeout_seconds=record.timeout_seconds,
                retry_safe=record.retry_safe,
                error=(
                    ToolErrorResponse(
                        error_id=record.error.error_id,
                        code=record.error.code,
                        category=record.error.category,
                        retryable=record.error.retryable,
                        recovery_action=record.error.recovery_action,
                        message=record.error.user_message,
                    )
                    if record.error is not None
                    else None
                ),
            )
            for record in info.tool_calls
        ],
    )
