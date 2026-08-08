from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from app.tools.draft_models import (
    ApplicationDraft,
    DraftStatus,
    DraftUserContext,
)
from app.tools.submission_models import (
    ApprovalWorkflowStepStatus,
    MockApprovalSubmissionResult,
    SubmissionAuditEvent,
    SubmissionAuditRecord,
    SubmissionStatus,
    SubmittedApplication,
    SubmittedApprovalStep,
    SubmittedApprovalWorkflow,
)


class SubmissionPreconditionError(ValueError):
    """草稿不满足模拟提交前置条件。"""

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


class SubmissionConflictError(RuntimeError):
    """同一草稿试图使用不同幂等键再次创建审批申请。"""


@dataclass(frozen=True, slots=True)
class _StoredSubmission:
    result: MockApprovalSubmissionResult
    session_id: str
    employee_id: str


_SUBMISSION_PREFIXES = {
    "purchase": "PUR",
    "travel_reimbursement": "TRV",
    "leave": "LEV",
    "expense_reimbursement": "EXP",
}
_SAFE_TOKEN_PATTERN = re.compile(r"[^A-Za-z0-9]")
_EXPLICIT_SUBMISSION_COMMANDS = {
    "提交",
    "提交审批",
    "提交申请",
    "正式提交",
    "确认提交",
    "确认提交审批",
}


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _required_text(value: str, *, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank")
    return normalized


def _has_blocking_issue(draft: ApplicationDraft) -> bool:
    return any(issue.blocking for issue in draft.validation_issues)


class MockApprovalSubmitter:
    """以进程内存模拟审批提交，并保证并发幂等。"""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: uuid4().hex)
        self._lock = asyncio.Lock()
        self._by_idempotency_key: dict[str, _StoredSubmission] = {}
        self._idempotency_key_by_draft: dict[str, str] = {}
        self._audit_records: list[SubmissionAuditRecord] = []

    def _token(self) -> str:
        token = _SAFE_TOKEN_PATTERN.sub("", self._token_factory()).upper()
        if not token:
            raise RuntimeError("token_factory returned no usable characters")
        return token

    @staticmethod
    def _validate_identity(
        draft: ApplicationDraft,
        user_context: DraftUserContext,
        session_id: str,
    ) -> None:
        if draft.applicant.employee_id != user_context.employee_id:
            raise SubmissionPreconditionError(
                "draft_owner_mismatch",
                "当前登录用户不是该草稿的申请人，不能提交审批。",
            )
        if draft.audit_metadata.created_by != user_context.employee_id:
            raise SubmissionPreconditionError(
                "draft_creator_mismatch",
                "草稿创建人校验失败，不能提交审批。",
            )
        if draft.audit_metadata.session_id != session_id:
            raise SubmissionPreconditionError(
                "session_mismatch",
                "草稿不属于当前会话，不能提交审批。",
            )

    @staticmethod
    def _validate_first_submission(draft: ApplicationDraft) -> None:
        if draft.status is not DraftStatus.CONFIRMED:
            raise SubmissionPreconditionError(
                "draft_not_confirmed",
                "草稿尚未经过明确确认，请先确认草稿再提交审批。",
            )
        if not draft.user_confirmed or draft.confirmed_at is None:
            raise SubmissionPreconditionError(
                "confirmation_missing",
                "草稿缺少可信的用户确认记录，不能提交审批。",
            )
        if draft.submitted or draft.submission_id is not None:
            raise SubmissionPreconditionError(
                "inconsistent_submission_state",
                "草稿提交状态不一致，请勿继续创建新的审批申请。",
            )
        if not draft.ready_for_confirmation:
            raise SubmissionPreconditionError(
                "draft_not_ready",
                "草稿尚未满足完整性校验，不能提交审批。",
            )
        if draft.missing_fields:
            raise SubmissionPreconditionError(
                "missing_fields",
                "草稿仍有必填字段缺失，不能提交审批。",
            )
        if _has_blocking_issue(draft):
            raise SubmissionPreconditionError(
                "blocking_validation_issue",
                "草稿仍有阻断性校验问题，不能提交审批。",
            )
        if draft.material_check.missing_materials:
            raise SubmissionPreconditionError(
                "missing_materials",
                "申请材料尚未齐全，不能提交审批。",
            )
        if draft.approval_check.clarification_question is not None:
            raise SubmissionPreconditionError(
                "approval_route_incomplete",
                "审批路线仍缺少关键信息，不能提交审批。",
            )
        if not draft.approval_check.steps:
            raise SubmissionPreconditionError(
                "approval_route_empty",
                "没有可用的审批路线，不能提交审批。",
            )

    def _submission_id(
        self,
        draft: ApplicationDraft,
        idempotency_key: str,
        submitted_at: datetime,
    ) -> str:
        prefix = _SUBMISSION_PREFIXES[draft.application_type.value]
        digest = sha256(
            (
                f"{draft.draft_id}\0{idempotency_key}\0"
                f"{submitted_at.isoformat()}\0{self._token()}"
            ).encode()
        ).hexdigest()[:12].upper()
        return (
            f"MOCK-{prefix}-{submitted_at:%Y%m%d}-{digest}"
        )

    def _audit_record(
        self,
        *,
        event: SubmissionAuditEvent,
        draft: ApplicationDraft,
        submission: SubmittedApplication,
        session_id: str,
        request_id: str,
        confirmation_text: str,
        recorded_at: datetime,
        duplicate_submission: bool,
    ) -> SubmissionAuditRecord:
        audit_digest = sha256(
            (
                f"{request_id}\0{submission.idempotency_key}\0"
                f"{event.value}\0{recorded_at.isoformat()}\0"
                f"{self._token()}"
            ).encode()
        ).hexdigest()[:16].upper()
        return SubmissionAuditRecord(
            audit_id=f"AUDIT-{audit_digest}",
            event=event,
            session_id=session_id,
            request_id=request_id,
            draft_id=draft.draft_id,
            draft_revision=draft.revision,
            submission_id=submission.submission_id,
            submission_idempotency_key=(
                submission.idempotency_key
            ),
            actor_employee_id=submission.submitted_by,
            recorded_at=recorded_at,
            confirmation_text_recorded=True,
            confirmation_text_sha256=sha256(
                confirmation_text.encode()
            ).hexdigest(),
            duplicate_submission=duplicate_submission,
            sensitive_fields_recorded=False,
        )

    async def submit(
        self,
        draft: ApplicationDraft,
        *,
        confirmation_text: str,
        user_context: DraftUserContext,
        session_id: str,
        request_id: str,
        submission_idempotency_key: str,
    ) -> MockApprovalSubmissionResult:
        """模拟创建审批申请；相同幂等键只创建一次。"""

        normalized_confirmation = _required_text(
            confirmation_text,
            name="confirmation_text",
        )
        normalized_command = normalized_confirmation.strip(
            "。.!！?？ ，,"
        )
        if normalized_command not in _EXPLICIT_SUBMISSION_COMMANDS:
            raise SubmissionPreconditionError(
                "explicit_submission_required",
                "提交指令不够明确，未创建审批申请。请明确回复“提交审批”。",
            )
        normalized_session_id = _required_text(
            session_id,
            name="session_id",
        )
        normalized_request_id = _required_text(
            request_id,
            name="request_id",
        )
        normalized_key = _required_text(
            submission_idempotency_key,
            name="submission_idempotency_key",
        )
        if len(normalized_key) < 8:
            raise ValueError(
                "submission_idempotency_key must contain at least 8 characters"
            )

        self._validate_identity(
            draft,
            user_context,
            normalized_session_id,
        )

        async with self._lock:
            existing = self._by_idempotency_key.get(normalized_key)
            if existing is not None:
                if (
                    existing.result.submission_result.draft_id
                    != draft.draft_id
                    or existing.employee_id
                    != user_context.employee_id
                    or existing.session_id != normalized_session_id
                ):
                    raise SubmissionConflictError(
                        "idempotency key is already bound to another submission"
                    )
                replayed_at = _aware_utc(self._clock())
                replay_audit = self._audit_record(
                    event=SubmissionAuditEvent.IDEMPOTENT_REPLAY,
                    draft=draft,
                    submission=existing.result.submission_result,
                    session_id=normalized_session_id,
                    request_id=normalized_request_id,
                    confirmation_text=normalized_confirmation,
                    recorded_at=replayed_at,
                    duplicate_submission=True,
                )
                self._audit_records.append(replay_audit)
                return replace(
                    existing.result,
                    duplicate_submission=True,
                    audit_record=replay_audit,
                )

            previous_key = self._idempotency_key_by_draft.get(
                draft.draft_id
            )
            if previous_key is not None:
                raise SubmissionConflictError(
                    "draft is already bound to another submission"
                )

            self._validate_first_submission(draft)
            submitted_at = _aware_utc(self._clock())
            submission_id = self._submission_id(
                draft,
                normalized_key,
                submitted_at,
            )
            submission = SubmittedApplication(
                submission_id=submission_id,
                draft_id=draft.draft_id,
                application_type=draft.application_type,
                status=SubmissionStatus.APPROVAL_IN_PROGRESS,
                submitted_at=submitted_at,
                submitted_by=user_context.employee_id,
                idempotency_key=normalized_key,
            )
            workflow = SubmittedApprovalWorkflow(
                workflow_id=f"WF-{submission_id}",
                current_step=1,
                steps=tuple(
                    SubmittedApprovalStep(
                        sequence=step.sequence,
                        approver=step.approver,
                        display_name=step.display_name,
                        status=(
                            ApprovalWorkflowStepStatus.PENDING
                            if index == 0
                            else ApprovalWorkflowStepStatus.WAITING
                        ),
                    )
                    for index, step in enumerate(
                        draft.approval_check.steps
                    )
                ),
            )
            audit = self._audit_record(
                event=SubmissionAuditEvent.SUBMITTED,
                draft=draft,
                submission=submission,
                session_id=normalized_session_id,
                request_id=normalized_request_id,
                confirmation_text=normalized_confirmation,
                recorded_at=submitted_at,
                duplicate_submission=False,
            )
            result = MockApprovalSubmissionResult(
                success=True,
                duplicate_submission=False,
                submission_result=submission,
                approval_workflow=workflow,
                audit_record=audit,
            )
            self._by_idempotency_key[normalized_key] = (
                _StoredSubmission(
                    result=result,
                    session_id=normalized_session_id,
                    employee_id=user_context.employee_id,
                )
            )
            self._idempotency_key_by_draft[draft.draft_id] = (
                normalized_key
            )
            self._audit_records.append(audit)
            return result

    async def list_audit_records(
        self,
        *,
        draft_id: str | None = None,
    ) -> tuple[SubmissionAuditRecord, ...]:
        """返回当前进程中的审计事件副本，主要用于本地验收。"""

        async with self._lock:
            if draft_id is None:
                return tuple(self._audit_records)
            return tuple(
                record
                for record in self._audit_records
                if record.draft_id == draft_id
            )
