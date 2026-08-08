from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.tools.approval_models import ApproverCode
from app.tools.material_models import ApplicationType


class SubmissionStatus(StrEnum):
    """模拟审批申请当前所处的状态。"""

    APPROVAL_IN_PROGRESS = "approval_in_progress"


class ApprovalWorkflowStepStatus(StrEnum):
    """模拟审批流中单个节点的等待状态。"""

    PENDING = "pending"
    WAITING = "waiting"


class SubmissionAuditEvent(StrEnum):
    """提交工具记录的安全审计事件。"""

    SUBMITTED = "submitted"
    IDEMPOTENT_REPLAY = "idempotent_replay"


@dataclass(frozen=True, slots=True)
class SubmittedApplication:
    """首次模拟提交后生成的正式申请记录。"""

    submission_id: str
    draft_id: str
    application_type: ApplicationType
    status: SubmissionStatus
    submitted_at: datetime
    submitted_by: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SubmittedApprovalStep:
    """模拟审批流中的一个有序节点。"""

    sequence: int
    approver: ApproverCode
    display_name: str
    status: ApprovalWorkflowStepStatus


@dataclass(frozen=True, slots=True)
class SubmittedApprovalWorkflow:
    """提交时冻结的模拟审批路线。"""

    workflow_id: str
    current_step: int
    steps: tuple[SubmittedApprovalStep, ...]


@dataclass(frozen=True, slots=True)
class SubmissionAuditRecord:
    """不记录草稿正文或敏感字段的最小提交审计事件。"""

    audit_id: str
    event: SubmissionAuditEvent
    session_id: str
    request_id: str
    draft_id: str
    draft_revision: int
    submission_id: str
    submission_idempotency_key: str
    actor_employee_id: str
    recorded_at: datetime
    confirmation_text_recorded: bool
    confirmation_text_sha256: str
    duplicate_submission: bool
    sensitive_fields_recorded: bool = False


@dataclass(frozen=True, slots=True)
class MockApprovalSubmissionResult:
    """模拟提交工具的一次结构化返回。"""

    success: bool
    duplicate_submission: bool
    submission_result: SubmittedApplication
    approval_workflow: SubmittedApprovalWorkflow
    audit_record: SubmissionAuditRecord
    storage_backend: str = "in_memory"
    survives_process_restart: bool = False
