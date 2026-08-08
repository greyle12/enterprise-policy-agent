from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from app.rag.policy_context import PolicyCitation
from app.tools.approval_models import ApprovalCheckResult
from app.tools.material_models import ApplicationType, MaterialCheckResult

type DraftValue = str | int | Decimal | bool


class DraftStatus(StrEnum):
    """申请草稿在生成后的业务状态。"""

    WAITING_FOR_INFORMATION = "waiting_for_information"
    WAITING_FOR_MATERIALS = "waiting_for_materials"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    CONFIRMED = "confirmed"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"


class DraftFieldSource(StrEnum):
    """草稿字段值的来源，便于审计自动填充行为。"""

    USER_INPUT = "user_input"
    CALCULATED = "calculated"
    TRUSTED_CONTEXT = "trusted_context"
    DETERMINISTIC_RULE = "deterministic_rule"


class ValidationSeverity(StrEnum):
    """草稿校验问题的严重程度。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DraftUserContext:
    """由可信身份层注入、禁止由用户消息覆盖的申请人信息。"""

    employee_id: str
    employee_name: str
    department: str
    roles: tuple[str, ...]
    region: str
    identity_source: str


@dataclass(frozen=True, slots=True)
class DraftField:
    """草稿中一项已经取得值的业务字段。"""

    field_name: str
    display_name: str
    value: DraftValue
    source: DraftFieldSource
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class MissingDraftField:
    """草稿仍缺少的一项必填字段。"""

    field_name: str
    display_name: str
    question: str


@dataclass(frozen=True, slots=True)
class DraftValidationIssue:
    """草稿生成期间发现的一项计算或数据问题。"""

    code: str
    severity: ValidationSeverity
    message: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class DraftPolicySnapshot:
    """生成草稿时使用的一份制度版本快照。"""

    document_id: str
    document_title: str
    version: str
    effective_date: date


@dataclass(frozen=True, slots=True)
class DraftAuditMetadata:
    """无持久化草稿仍需返回的最小审计信息。"""

    session_id: str
    request_id: str
    idempotency_key: str
    created_at: datetime
    created_by: str
    identity_source: str
    persisted: bool


@dataclass(frozen=True, slots=True)
class ApplicationDraft:
    """由确定性抽取、材料规则和审批规则共同生成的申请草稿。"""

    draft_id: str
    application_type: ApplicationType
    title: str
    status: DraftStatus
    applicant: DraftUserContext
    fields: tuple[DraftField, ...]
    missing_fields: tuple[MissingDraftField, ...]
    material_check: MaterialCheckResult
    approval_check: ApprovalCheckResult
    policy_snapshots: tuple[DraftPolicySnapshot, ...]
    validation_issues: tuple[DraftValidationIssue, ...]
    summary_lines: tuple[str, ...]
    warnings: tuple[str, ...]
    ready_for_confirmation: bool
    confirmation_required: bool
    user_confirmed: bool
    submitted: bool
    audit_metadata: DraftAuditMetadata
    revision: int = 1
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    submission_id: str | None = None
    submitted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DraftGenerationResult:
    """一次草稿生成请求的结构化结果。"""

    application_type: ApplicationType | None
    draft: ApplicationDraft | None
    clarification_question: str | None
    citations: tuple[PolicyCitation, ...]


@dataclass(frozen=True, slots=True)
class DraftGenerationAnswer:
    """供 AgentRouter 使用的草稿生成回答。"""

    request: str
    result: DraftGenerationResult
    reply: str
