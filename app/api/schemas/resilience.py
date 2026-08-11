from __future__ import annotations

from pydantic import BaseModel

from app.resilience import (
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
