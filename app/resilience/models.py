from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ToolName(StrEnum):
    """统一 Agent 工作流中受容错层保护的工具。"""

    INTENT_CLASSIFIER = "intent_classifier"
    POLICY_ANSWER = "policy_answer"
    MATERIAL_CHECK = "material_check"
    APPROVAL_CHECK = "approval_check"
    DRAFT_GENERATION = "draft_generation"
    DRAFT_REVISION = "draft_revision"
    APPROVAL_SUBMISSION = "approval_submission"


class ToolOperationKind(StrEnum):
    """工具副作用等级；它决定是否允许自动重试。"""

    READ_ONLY = "read_only"
    PURE_COMPUTATION = "pure_computation"
    MUTATION = "mutation"


class ToolCallOutcome(StrEnum):
    """一次逻辑工具调用经过容错层后的最终结果。"""

    SUCCESS = "success"
    RECOVERED = "recovered"
    FAILED = "failed"


class ToolFailureCategory(StrEnum):
    """不包含底层异常正文的稳定错误分类。"""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    INVALID_RESPONSE = "invalid_response"
    INTERNAL_ERROR = "internal_error"


class ToolRecoveryAction(StrEnum):
    """提供给 API 调用方的下一步动作。"""

    RETRY_LATER = "retry_later"
    RESUBMIT_WITH_SAME_SESSION = "resubmit_with_same_session"
    CONTACT_SUPPORT = "contact_support"


@dataclass(frozen=True, slots=True)
class ToolErrorInfo:
    """可以安全返回给调用方的工具错误，不包含原始异常文本。"""

    error_id: str
    code: str
    category: ToolFailureCategory
    retryable: bool
    recovery_action: ToolRecoveryAction
    user_message: str


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """一次逻辑工具调用的重试和降级证据。"""

    tool: ToolName
    operation: ToolOperationKind
    outcome: ToolCallOutcome
    attempts: int
    max_attempts: int
    timeout_seconds: float
    retry_safe: bool
    error: ToolErrorInfo | None = None


@dataclass(frozen=True, slots=True)
class AgentResilienceInfo:
    """一次 Agent 请求的容错摘要。"""

    degraded: bool
    recovered: bool
    tool_calls: tuple[ToolCallRecord, ...]
