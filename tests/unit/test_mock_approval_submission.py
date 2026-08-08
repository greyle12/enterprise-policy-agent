from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import (
    ApplicationDraft,
    DraftStatus,
    DraftUserContext,
)
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.mock_approval_submission import (
    MockApprovalSubmitter,
    SubmissionConflictError,
    SubmissionPreconditionError,
)
from app.tools.submission_models import (
    ApprovalWorkflowStepStatus,
    SubmissionAuditEvent,
    SubmissionStatus,
)

_POLICY_DIRECTORY = (
    Path(__file__).resolve().parents[2] / "data" / "policies"
)
_SESSION_ID = "mock-submission-unit"
_CONFIRMED_AT = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
_SUBMITTED_AT = datetime(2026, 8, 8, 9, 30, tzinfo=UTC)
_USER_CONTEXT = DraftUserContext(
    employee_id="DEMO-EMP-001",
    employee_name="演示用户",
    department="演示部门",
    roles=("EMPLOYEE",),
    region="中国大陆",
    identity_source="trusted_demo_context",
)
_COMPLETE_PURCHASE = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)


async def _draft(
    *,
    confirmed: bool = True,
    session_id: str = _SESSION_ID,
) -> ApplicationDraft:
    material_checker = RequiredMaterialsChecker.from_policy_directory(
        _POLICY_DIRECTORY
    )
    approval_checker = ApprovalRuleChecker.from_policy_directory(
        _POLICY_DIRECTORY
    )
    generator = ApplicationDraftGenerator.from_policy_directory(
        _POLICY_DIRECTORY,
        material_checker=material_checker,
        approval_checker=approval_checker,
        user_context=_USER_CONTEXT,
        clock=lambda: _CONFIRMED_AT,
    )
    answer = await generator.generate(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    assert answer.result.draft is not None
    generated = answer.result.draft
    assert generated.ready_for_confirmation
    if not confirmed:
        return generated
    return replace(
        generated,
        status=DraftStatus.CONFIRMED,
        confirmation_required=False,
        user_confirmed=True,
        confirmed_at=_CONFIRMED_AT,
    )


async def _submit(
    service: MockApprovalSubmitter,
    draft: ApplicationDraft,
    *,
    key: str = "submission-idempotency-key-001",
    request_id: str = "SUBMIT-REQUEST-001",
    session_id: str = _SESSION_ID,
    user_context: DraftUserContext = _USER_CONTEXT,
):
    return await service.submit(
        draft,
        confirmation_text="提交审批",
        user_context=user_context,
        session_id=session_id,
        request_id=request_id,
        submission_idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_submits_confirmed_draft_and_freezes_approval_route() -> None:
    draft = await _draft()
    service = MockApprovalSubmitter(
        clock=lambda: _SUBMITTED_AT,
        token_factory=lambda: "TOKEN-001",
    )

    result = await _submit(service, draft)

    assert result.success is True
    assert result.duplicate_submission is False
    assert result.storage_backend == "in_memory"
    assert result.survives_process_restart is False
    submission = result.submission_result
    assert submission.submission_id.startswith("MOCK-PUR-20260808-")
    assert submission.draft_id == draft.draft_id
    assert submission.status is SubmissionStatus.APPROVAL_IN_PROGRESS
    assert submission.submitted_at == _SUBMITTED_AT
    assert submission.submitted_by == _USER_CONTEXT.employee_id
    assert result.approval_workflow.workflow_id == (
        f"WF-{submission.submission_id}"
    )
    assert result.approval_workflow.current_step == 1
    assert [step.approver for step in result.approval_workflow.steps] == [
        step.approver for step in draft.approval_check.steps
    ]
    assert result.approval_workflow.steps[0].status is (
        ApprovalWorkflowStepStatus.PENDING
    )
    assert all(
        step.status is ApprovalWorkflowStepStatus.WAITING
        for step in result.approval_workflow.steps[1:]
    )


@pytest.mark.asyncio
async def test_rejects_draft_without_explicit_confirmation() -> None:
    draft = await _draft(confirmed=False)
    service = MockApprovalSubmitter()

    with pytest.raises(SubmissionPreconditionError) as exc_info:
        await _submit(service, draft)

    assert exc_info.value.code == "draft_not_confirmed"
    assert await service.list_audit_records() == ()


@pytest.mark.asyncio
async def test_direct_tool_rejects_ambiguous_submission_text() -> None:
    draft = await _draft()
    service = MockApprovalSubmitter()

    with pytest.raises(SubmissionPreconditionError) as exc_info:
        await service.submit(
            draft,
            confirmation_text="现在可以提交审批吗？",
            user_context=_USER_CONTEXT,
            session_id=_SESSION_ID,
            request_id="AMBIGUOUS-REQUEST",
            submission_idempotency_key=(
                "submission-idempotency-ambiguous"
            ),
        )

    assert exc_info.value.code == "explicit_submission_required"
    assert await service.list_audit_records() == ()


@pytest.mark.asyncio
async def test_rejects_draft_owner_or_session_mismatch() -> None:
    draft = await _draft()
    service = MockApprovalSubmitter()
    other_user = replace(
        _USER_CONTEXT,
        employee_id="DEMO-EMP-999",
    )

    with pytest.raises(SubmissionPreconditionError) as owner_error:
        await _submit(service, draft, user_context=other_user)
    with pytest.raises(SubmissionPreconditionError) as session_error:
        await _submit(
            service,
            draft,
            session_id="another-session",
        )

    assert owner_error.value.code == "draft_owner_mismatch"
    assert session_error.value.code == "session_mismatch"


@pytest.mark.asyncio
async def test_rejects_incomplete_draft_even_if_status_is_forged() -> None:
    incomplete = await _draft(confirmed=False)
    forged = replace(
        incomplete,
        status=DraftStatus.CONFIRMED,
        ready_for_confirmation=False,
        user_confirmed=True,
        confirmed_at=_CONFIRMED_AT,
    )

    with pytest.raises(SubmissionPreconditionError) as exc_info:
        await _submit(MockApprovalSubmitter(), forged)

    assert exc_info.value.code == "draft_not_ready"


@pytest.mark.asyncio
async def test_repeated_key_returns_first_result_and_records_replay() -> None:
    draft = await _draft()
    times = iter(
        (
            _SUBMITTED_AT,
            datetime(2026, 8, 8, 9, 31, tzinfo=UTC),
        )
    )
    service = MockApprovalSubmitter(clock=lambda: next(times))

    first = await _submit(service, draft, request_id="REQUEST-FIRST")
    replay = await _submit(service, draft, request_id="REQUEST-REPLAY")

    assert replay.duplicate_submission is True
    assert replay.submission_result == first.submission_result
    assert replay.approval_workflow == first.approval_workflow
    assert replay.audit_record.event is SubmissionAuditEvent.IDEMPOTENT_REPLAY
    assert replay.audit_record.request_id == "REQUEST-REPLAY"
    records = await service.list_audit_records(draft_id=draft.draft_id)
    assert [record.event for record in records] == [
        SubmissionAuditEvent.SUBMITTED,
        SubmissionAuditEvent.IDEMPOTENT_REPLAY,
    ]


@pytest.mark.asyncio
async def test_concurrent_retries_create_exactly_one_submission() -> None:
    draft = await _draft()
    service = MockApprovalSubmitter()

    results = await asyncio.gather(
        *(
            _submit(
                service,
                draft,
                request_id=f"CONCURRENT-{index}",
            )
            for index in range(8)
        )
    )

    assert len(
        {
            result.submission_result.submission_id
            for result in results
        }
    ) == 1
    assert sum(not result.duplicate_submission for result in results) == 1
    assert sum(result.duplicate_submission for result in results) == 7
    records = await service.list_audit_records()
    assert len(records) == 8
    assert sum(
        record.event is SubmissionAuditEvent.SUBMITTED
        for record in records
    ) == 1


@pytest.mark.asyncio
async def test_same_draft_cannot_bypass_idempotency_with_another_key() -> None:
    draft = await _draft()
    service = MockApprovalSubmitter()
    await _submit(service, draft, key="submission-key-original")

    with pytest.raises(SubmissionConflictError):
        await _submit(service, draft, key="submission-key-different")


@pytest.mark.asyncio
async def test_audit_record_hashes_confirmation_and_omits_sensitive_fields() -> None:
    draft = await _draft()
    result = await _submit(MockApprovalSubmitter(), draft)
    audit = result.audit_record

    assert audit.event is SubmissionAuditEvent.SUBMITTED
    assert audit.confirmation_text_recorded is True
    assert len(audit.confirmation_text_sha256) == 64
    assert audit.confirmation_text_sha256 != "提交审批"
    assert audit.sensitive_fields_recorded is False
    assert audit.draft_revision == draft.revision


@pytest.mark.asyncio
async def test_normalizes_naive_clock_to_utc() -> None:
    draft = await _draft()
    service = MockApprovalSubmitter(
        clock=lambda: datetime.fromisoformat("2026-08-08T09:30:00"),
    )

    result = await _submit(service, draft)

    assert result.submission_result.submitted_at.tzinfo is UTC
    assert result.audit_record.recorded_at.tzinfo is UTC
