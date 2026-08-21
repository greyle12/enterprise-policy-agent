from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentRouter,
    AgentSessionPhase,
    AgentWorkflowNode,
)
from app.rag.policy_answer_service import PolicyAnswer
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftStatus, DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.mock_approval_submission import MockApprovalSubmitter
from app.tools.submission_models import SubmissionAuditEvent

_POLICY_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "policies"
_COMPLETE_PURCHASE = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)


class DeterministicIntentClassifier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def classify(
        self,
        user_input: str,
    ) -> IntentClassification:
        self.calls.append(user_input)
        intent = (
            IntentType.DRAFT_GENERATION
            if "草稿" in user_input and "生成" in user_input
            else IntentType.UNKNOWN
        )
        return IntentClassification(
            intent=intent,
            confidence=1.0,
            reason="Day 14 确定性测试分类。",
        )


class StubPolicyAnswerService:
    async def answer(self, question: str) -> PolicyAnswer:
        return PolicyAnswer(
            question=question,
            answer="本测试不验证制度问答。",
            citations=(),
        )


def _build_router() -> tuple[
    AgentRouter,
    MockApprovalSubmitter,
    DeterministicIntentClassifier,
]:
    classifier = DeterministicIntentClassifier()
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    submission_service = MockApprovalSubmitter()
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=StubPolicyAnswerService(),
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=ApplicationDraftGenerator.from_policy_directory(
            _POLICY_DIRECTORY,
            material_checker=material_checker,
            approval_checker=approval_checker,
            user_context=DraftUserContext(
                employee_id="DEMO-EMP-001",
                employee_name="演示用户",
                department="演示部门",
                roles=("EMPLOYEE",),
                region="中国大陆",
                identity_source="trusted_demo_context",
            ),
        ),
        submission_service=submission_service,
    )
    return router, submission_service, classifier


def _draft(result):
    assert result.application_draft is not None
    assert result.application_draft.draft is not None
    return result.application_draft.draft


async def _confirmed(router: AgentRouter, session_id: str):
    created = await router.route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    assert created.status is AgentResponseStatus.AWAITING_CONFIRMATION
    return await router.route(
        "确认草稿",
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_confirmed_draft_can_be_submitted_in_separate_turn() -> None:
    router, _, _ = _build_router()
    session_id = "agent-submit-success"
    confirmed = await _confirmed(router, session_id)

    submitted = await router.route(
        "提交审批",
        session_id=session_id,
    )

    draft = _draft(submitted)
    assert confirmed.status is AgentResponseStatus.CONFIRMED
    assert submitted.status is AgentResponseStatus.SUBMITTED
    assert submitted.classification.intent is IntentType.DRAFT_SUBMISSION
    assert draft.status is DraftStatus.SUBMITTED
    assert draft.user_confirmed is True
    assert draft.submitted is True
    assert draft.submission_id is not None
    assert draft.submitted_at is not None
    assert submitted.submission is not None
    assert submitted.submission.duplicate_submission is False
    assert submitted.submission.submission_result.submission_id == (draft.submission_id)
    assert submitted.session is not None
    assert submitted.session.phase is AgentSessionPhase.SUBMITTED
    assert submitted.session.pending_confirmation is False
    assert submitted.workflow is not None
    assert [step.node for step in submitted.workflow.steps] == [
        AgentWorkflowNode.RESOLVE_TURN,
        AgentWorkflowNode.SUBMIT_APPROVAL,
    ]


@pytest.mark.asyncio
async def test_submit_before_confirmation_is_rejected_and_reinterrupts() -> None:
    router, service, _ = _build_router()
    session_id = "agent-submit-before-confirm"
    created = await router.route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )

    rejected = await router.route(
        "确认提交",
        session_id=session_id,
    )

    assert created.status is AgentResponseStatus.AWAITING_CONFIRMATION
    assert rejected.status is AgentResponseStatus.AWAITING_CONFIRMATION
    assert rejected.classification.intent is IntentType.DRAFT_SUBMISSION
    assert _draft(rejected).submitted is False
    assert rejected.session is not None
    assert rejected.session.pending_confirmation is True
    assert rejected.workflow is not None
    assert rejected.workflow.interrupted is True
    assert AgentWorkflowNode.SUBMIT_APPROVAL in {step.node for step in rejected.workflow.steps}
    assert await service.list_audit_records() == ()

    confirmed = await router.route(
        "确认草稿",
        session_id=session_id,
    )
    assert confirmed.status is AgentResponseStatus.CONFIRMED


@pytest.mark.asyncio
async def test_repeated_submission_is_idempotent() -> None:
    router, service, _ = _build_router()
    session_id = "agent-submit-retry"
    await _confirmed(router, session_id)
    first = await router.route("提交审批", session_id=session_id)

    replay = await router.route("提交审批", session_id=session_id)

    assert first.submission is not None
    assert replay.submission is not None
    assert replay.status is AgentResponseStatus.SUBMITTED
    assert replay.submission.duplicate_submission is True
    assert replay.submission.submission_result == (first.submission.submission_result)
    assert replay.submission.audit_record.event is (SubmissionAuditEvent.IDEMPOTENT_REPLAY)
    records = await service.list_audit_records()
    assert [record.event for record in records] == [
        SubmissionAuditEvent.SUBMITTED,
        SubmissionAuditEvent.IDEMPOTENT_REPLAY,
    ]


@pytest.mark.asyncio
async def test_submit_without_active_draft_does_not_call_classifier() -> None:
    router, service, classifier = _build_router()

    result = await router.route(
        "提交审批",
        session_id="agent-submit-missing",
    )

    assert result.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert result.classification.intent is IntentType.DRAFT_SUBMISSION
    assert result.application_draft is None
    assert classifier.calls == []
    assert result.workflow is not None
    assert result.workflow.terminal_node is (AgentWorkflowNode.SUBMIT_APPROVAL)
    assert await service.list_audit_records() == ()


@pytest.mark.asyncio
async def test_submission_question_has_no_side_effect() -> None:
    router, service, classifier = _build_router()
    session_id = "agent-submit-question"
    await _confirmed(router, session_id)

    result = await router.route(
        "现在可以提交审批吗？",
        session_id=session_id,
    )

    assert result.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert result.classification.intent is IntentType.UNKNOWN
    assert result.submission is None
    assert classifier.calls[-1] == "现在可以提交审批吗？"
    assert await service.list_audit_records() == ()


@pytest.mark.asyncio
async def test_submitted_draft_cannot_be_modified_or_cancelled() -> None:
    router, service, _ = _build_router()
    session_id = "agent-submit-immutable"
    await _confirmed(router, session_id)
    submitted = await router.route("提交审批", session_id=session_id)
    submission_id = _draft(submitted).submission_id

    modified = await router.route(
        "把预计单价改为2200元",
        session_id=session_id,
    )
    cancelled = await router.route(
        "取消草稿",
        session_id=session_id,
    )

    assert modified.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert cancelled.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert _draft(modified).submission_id == submission_id
    assert _draft(cancelled).submission_id == submission_id
    assert _draft(cancelled).status is DraftStatus.SUBMITTED
    assert cancelled.session is not None
    assert cancelled.session.phase is AgentSessionPhase.SUBMITTED
    assert len(await service.list_audit_records()) == 1


@pytest.mark.asyncio
async def test_cancelled_draft_cannot_be_submitted() -> None:
    router, service, _ = _build_router()
    session_id = "agent-submit-cancelled"
    await router.route(_COMPLETE_PURCHASE, session_id=session_id)
    await router.route("取消草稿", session_id=session_id)

    rejected = await router.route("提交审批", session_id=session_id)

    assert rejected.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert _draft(rejected).status is DraftStatus.CANCELLED
    assert rejected.session is not None
    assert rejected.session.phase is AgentSessionPhase.CANCELLED
    assert await service.list_audit_records() == ()


@pytest.mark.asyncio
async def test_sessions_create_independent_drafts_and_submissions() -> None:
    router, service, _ = _build_router()
    await _confirmed(router, "agent-submit-session-a")
    await _confirmed(router, "agent-submit-session-b")

    first = await router.route(
        "提交审批",
        session_id="agent-submit-session-a",
    )
    second = await router.route(
        "提交审批",
        session_id="agent-submit-session-b",
    )

    assert _draft(first).draft_id != _draft(second).draft_id
    assert _draft(first).submission_id != _draft(second).submission_id
    assert len(await service.list_audit_records()) == 2
