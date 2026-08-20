from __future__ import annotations

from decimal import Decimal
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
from app.tools.draft_models import (
    ApplicationDraft,
    DraftStatus,
    DraftUserContext,
)
from app.tools.material_check import RequiredMaterialsChecker

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
        if "草稿" in user_input or "生成" in user_input:
            intent = IntentType.DRAFT_GENERATION
        elif "标准" in user_input or "制度" in user_input:
            intent = IntentType.POLICY_QUERY
        else:
            intent = IntentType.UNKNOWN
        return IntentClassification(
            intent=intent,
            confidence=1.0,
            reason="确定性测试分类",
        )


class FakePolicyAnswerService:
    async def answer(self, question: str) -> PolicyAnswer:
        return PolicyAnswer(
            question=question,
            answer="测试制度回答。",
            citations=(),
        )


def _build_router() -> tuple[
    AgentRouter,
    DeterministicIntentClassifier,
]:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    classifier = DeterministicIntentClassifier()
    draft_generator = ApplicationDraftGenerator.from_policy_directory(
        _POLICY_DIRECTORY,
        material_checker=material_checker,
        approval_checker=approval_checker,
        user_context=DraftUserContext(
            employee_id="TEST-EMP-001",
            employee_name="测试用户",
            department="测试部门",
            roles=("EMPLOYEE",),
            region="中国大陆",
            identity_source="trusted_test_context",
        ),
    )
    return (
        AgentRouter(
            intent_classifier=classifier,
            policy_answer_service=FakePolicyAnswerService(),
            material_checker=material_checker,
            approval_checker=approval_checker,
            draft_generator=draft_generator,
        ),
        classifier,
    )


def _draft_from(result) -> ApplicationDraft:
    assert result.application_draft is not None
    assert result.application_draft.draft is not None
    return result.application_draft.draft


def _field_value(
    draft: ApplicationDraft,
    field_name: str,
):
    return next(field.value for field in draft.fields if field.field_name == field_name)


@pytest.mark.asyncio
async def test_completes_and_confirms_draft_across_turns() -> None:
    router, classifier = _build_router()
    session_id = "conversation-complete-001"

    first = await router.route(
        "帮我生成采购申请草稿。",
        session_id=session_id,
    )
    first_draft = _draft_from(first)

    assert first.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert first_draft.status is DraftStatus.WAITING_FOR_INFORMATION
    assert first_draft.revision == 1
    assert first_draft.audit_metadata.session_id == session_id
    assert first.session is not None
    assert first.session.turn_number == 1
    assert first.session.phase is AgentSessionPhase.COLLECTING_INFORMATION

    second = await router.route(
        _COMPLETE_PURCHASE.replace(
            "帮我生成采购申请草稿，",
            "补充信息：",
        ),
        session_id=session_id,
    )
    second_draft = _draft_from(second)

    assert second.status is AgentResponseStatus.AWAITING_CONFIRMATION
    assert second.classification.intent is IntentType.DRAFT_UPDATE
    assert second_draft.draft_id == first_draft.draft_id
    assert second_draft.revision == 2
    assert second_draft.ready_for_confirmation is True
    assert second.session is not None
    assert second.session.pending_confirmation is True
    assert second.workflow is not None
    assert second.workflow.interrupted is True
    assert second.workflow.terminal_node is (AgentWorkflowNode.AWAIT_CONFIRMATION)

    confirmed = await router.route(
        "确认草稿",
        session_id=session_id,
    )
    confirmed_draft = _draft_from(confirmed)

    assert confirmed.status is AgentResponseStatus.CONFIRMED
    assert confirmed.classification.intent is IntentType.DRAFT_CONFIRMATION
    assert confirmed_draft.draft_id == first_draft.draft_id
    assert confirmed_draft.status is DraftStatus.CONFIRMED
    assert confirmed_draft.user_confirmed is True
    assert confirmed_draft.submitted is False
    assert confirmed_draft.confirmed_at is not None
    assert confirmed.session is not None
    assert confirmed.session.turn_number == 3
    assert confirmed.session.pending_confirmation is False
    assert confirmed.session.phase is AgentSessionPhase.CONFIRMED
    assert classifier.calls == ["帮我生成采购申请草稿。"]


@pytest.mark.asyncio
async def test_modification_resumes_and_reinterrupts_workflow() -> None:
    router, classifier = _build_router()
    session_id = "conversation-revise-001"

    created = await router.route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    original = _draft_from(created)

    revised = await router.route(
        "把预计单价改为2200元",
        session_id=session_id,
    )
    revised_draft = _draft_from(revised)

    assert revised.status is AgentResponseStatus.AWAITING_CONFIRMATION
    assert revised_draft.draft_id == original.draft_id
    assert revised_draft.revision == 2
    assert _field_value(
        revised_draft,
        "estimated_unit_price",
    ) == Decimal(2200)
    assert _field_value(
        revised_draft,
        "estimated_total_amount",
    ) == Decimal(6600)
    assert revised_draft.approval_check.amount == Decimal(6600)
    assert revised_draft.user_confirmed is False
    assert revised.session is not None
    assert revised.session.pending_confirmation is True
    assert revised.workflow is not None
    assert [step.node for step in revised.workflow.steps] == [
        AgentWorkflowNode.RESOLVE_TURN,
        AgentWorkflowNode.HUMAN_CONFIRMATION_GATE,
        AgentWorkflowNode.UPDATE_DRAFT,
        AgentWorkflowNode.AWAIT_CONFIRMATION,
    ]
    assert classifier.calls == [_COMPLETE_PURCHASE]


@pytest.mark.asyncio
async def test_quantity_revision_recalculates_total() -> None:
    router, _ = _build_router()
    session_id = "conversation-quantity-001"

    await router.route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    revised = await router.route(
        "采购数量改为4台",
        session_id=session_id,
    )
    draft = _draft_from(revised)

    assert _field_value(draft, "quantity") == 4
    assert _field_value(
        draft,
        "estimated_total_amount",
    ) == Decimal(8000)
    assert draft.approval_check.amount == Decimal(8000)
    assert draft.ready_for_confirmation is True


@pytest.mark.asyncio
async def test_ambiguous_reply_does_not_resume_confirmation() -> None:
    router, _ = _build_router()
    session_id = "conversation-ambiguous-001"

    created = await router.route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    draft_id = _draft_from(created).draft_id

    ambiguous = await router.route(
        "好的，我再看看",
        session_id=session_id,
    )

    assert ambiguous.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert ambiguous.classification.intent is IntentType.UNKNOWN
    assert _draft_from(ambiguous).draft_id == draft_id
    assert ambiguous.session is not None
    assert ambiguous.session.turn_number == 1
    assert ambiguous.session.pending_confirmation is True

    confirmed = await router.route(
        "确认草稿",
        session_id=session_id,
    )
    assert confirmed.status is AgentResponseStatus.CONFIRMED
    assert confirmed.session is not None
    assert confirmed.session.turn_number == 2


@pytest.mark.asyncio
async def test_cancels_waiting_draft_without_submission() -> None:
    router, _ = _build_router()
    session_id = "conversation-cancel-001"

    await router.route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    cancelled = await router.route(
        "取消草稿",
        session_id=session_id,
    )
    draft = _draft_from(cancelled)

    assert cancelled.status is AgentResponseStatus.CANCELLED
    assert draft.status is DraftStatus.CANCELLED
    assert draft.user_confirmed is False
    assert draft.submitted is False
    assert draft.cancelled_at is not None
    assert cancelled.session is not None
    assert cancelled.session.phase is AgentSessionPhase.CANCELLED


@pytest.mark.asyncio
async def test_rejects_confirmation_for_incomplete_draft() -> None:
    router, _ = _build_router()
    session_id = "conversation-incomplete-001"

    await router.route(
        "帮我生成采购申请草稿。",
        session_id=session_id,
    )
    result = await router.route(
        "确认草稿",
        session_id=session_id,
    )
    draft = _draft_from(result)

    assert result.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert draft.status is DraftStatus.WAITING_FOR_INFORMATION
    assert draft.user_confirmed is False
    assert draft.submitted is False
    assert result.session is not None
    assert result.session.phase is AgentSessionPhase.COLLECTING_INFORMATION


@pytest.mark.asyncio
async def test_sessions_are_isolated_by_thread_id() -> None:
    router, _ = _build_router()

    first = await router.route(
        _COMPLETE_PURCHASE,
        session_id="isolated-session-a",
    )
    second = await router.route(
        _COMPLETE_PURCHASE.replace("3台", "4台"),
        session_id="isolated-session-b",
    )
    confirmed_first = await router.route(
        "确认草稿",
        session_id="isolated-session-a",
    )

    first_draft = _draft_from(first)
    second_draft = _draft_from(second)
    confirmed_draft = _draft_from(confirmed_first)
    assert first_draft.draft_id != second_draft.draft_id
    assert confirmed_draft.draft_id == first_draft.draft_id
    assert confirmed_draft.user_confirmed is True

    second_still_pending = await router.route(
        "好的",
        session_id="isolated-session-b",
    )
    assert _draft_from(second_still_pending).user_confirmed is False
    assert second_still_pending.session is not None
    assert second_still_pending.session.pending_confirmation is True


@pytest.mark.asyncio
async def test_clear_session_removes_memory_checkpoint() -> None:
    router, _ = _build_router()
    session_id = "conversation-clear-001"

    await router.route(
        _COMPLETE_PURCHASE,
        session_id=session_id,
    )
    await router.clear_session(session_id)
    result = await router.route(
        "确认草稿",
        session_id=session_id,
    )

    assert result.status is AgentResponseStatus.NEEDS_CLARIFICATION
    assert result.application_draft is not None
    assert result.application_draft.draft is None
    assert result.session is not None
    assert result.session.turn_number == 1
    assert result.session.phase is AgentSessionPhase.IDLE


@pytest.mark.asyncio
async def test_router_generates_session_id_when_omitted() -> None:
    router, _ = _build_router()

    result = await router.route("查询差旅制度标准")

    assert result.session is not None
    assert result.session.session_id.startswith("session-")
    assert len(result.session.session_id) == 40


@pytest.mark.asyncio
async def test_router_rejects_invalid_session_id() -> None:
    router, _ = _build_router()

    with pytest.raises(ValueError, match="session_id"):
        await router.route(
            "查询差旅制度标准",
            session_id="invalid/session",
        )
