from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import AgentResponseStatus, AgentRouter, AgentSessionPhase
from app.rag.policy_answer_service import PolicyAnswer
from app.resilience import (
    ResilientToolExecutor,
    ToolCallOutcome,
    ToolFailureCategory,
    ToolName,
    ToolRecoveryAction,
)
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftStatus, DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker
from app.tools.mock_approval_submission import MockApprovalSubmitter

_POLICY_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "policies"
_COMPLETE_PURCHASE = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)
_USER_CONTEXT = DraftUserContext(
    employee_id="DEMO-EMP-001",
    employee_name="演示用户",
    department="演示部门",
    roles=("EMPLOYEE",),
    region="中国大陆",
    identity_source="trusted_demo_context",
)


def _executor() -> ResilientToolExecutor:
    return ResilientToolExecutor(
        safe_tool_timeout_seconds=0.2,
        mutation_tool_timeout_seconds=0.2,
        max_attempts=3,
        retry_min_wait_seconds=0,
        retry_max_wait_seconds=0,
        error_id_factory=lambda: "ERR-WORKFLOW0001",
    )


class StaticClassifier:
    def __init__(self, intent: IntentType) -> None:
        self.intent = intent
        self.calls = 0

    async def classify(self, user_input: str) -> IntentClassification:
        self.calls += 1
        return IntentClassification(
            intent=self.intent,
            confidence=1.0,
            reason="Day 20 容错测试分类。",
        )


class FlakyClassifier(StaticClassifier):
    async def classify(self, user_input: str) -> IntentClassification:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("temporary classifier outage")
        return IntentClassification(
            intent=self.intent,
            confidence=1.0,
            reason="重试后分类成功。",
        )


class FailingClassifier(StaticClassifier):
    async def classify(self, user_input: str) -> IntentClassification:
        self.calls += 1
        raise ConnectionError("token=classifier-secret-must-not-leak")


class StaticPolicyService:
    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, question: str) -> PolicyAnswer:
        self.calls += 1
        return PolicyAnswer(
            question=question,
            answer="制度证据回答。",
            citations=(),
        )


class FailingPolicyService(StaticPolicyService):
    async def answer(self, question: str) -> PolicyAnswer:
        self.calls += 1
        raise ConnectionError("api_key=must-not-leak")


class UnusedTool:
    async def check(self, user_input: str):
        raise AssertionError(f"unused check tool called: {user_input}")

    async def generate(self, user_input: str, *, session_id=None):
        raise AssertionError(f"unused draft tool called: {user_input}")

    async def revise(
        self,
        previous_draft,
        user_input: str,
        *,
        session_id=None,
        context_messages=(),
    ):
        raise AssertionError(f"unused draft revision called: {user_input}")


class InvalidMaterialChecker:
    def __init__(self) -> None:
        self.calls = 0

    async def check(self, user_input: str):
        self.calls += 1
        raise RuntimeError("password=must-not-leak")


class DraftClassifier:
    async def classify(self, user_input: str) -> IntentClassification:
        return IntentClassification(
            intent=(
                IntentType.DRAFT_GENERATION
                if "生成" in user_input and "草稿" in user_input
                else IntentType.UNKNOWN
            ),
            confidence=1.0,
            reason="Day 20 草稿流程测试分类。",
        )


class FailingRevisionGenerator:
    def __init__(self, delegate: ApplicationDraftGenerator) -> None:
        self._delegate = delegate
        self.revision_calls = 0

    async def generate(self, user_input: str, *, session_id=None):
        return await self._delegate.generate(
            user_input,
            session_id=session_id,
        )

    async def revise(
        self,
        previous_draft,
        user_input: str,
        *,
        session_id=None,
        context_messages=(),
    ):
        self.revision_calls += 1
        raise ConnectionError("temporary draft service outage")


class FailOnceSubmitter:
    def __init__(self) -> None:
        self.calls = 0
        self.delegate = MockApprovalSubmitter()

    async def submit(self, draft, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("temporary submission connection loss")
        return await self.delegate.submit(draft, **kwargs)


def _business_tools():
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    draft_generator = ApplicationDraftGenerator.from_policy_directory(
        _POLICY_DIRECTORY,
        material_checker=material_checker,
        approval_checker=approval_checker,
        user_context=_USER_CONTEXT,
    )
    return material_checker, approval_checker, draft_generator


@pytest.mark.asyncio
async def test_transient_classifier_failure_recovers_and_continues() -> None:
    classifier = FlakyClassifier(IntentType.POLICY_QUERY)
    policy_service = StaticPolicyService()
    unused = UnusedTool()
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=policy_service,
        material_checker=unused,
        approval_checker=unused,
        draft_generator=unused,
        tool_executor=_executor(),
    )

    result = await router.route("差旅住宿标准是多少？")

    assert result.status is AgentResponseStatus.COMPLETED
    assert classifier.calls == 2
    assert policy_service.calls == 1
    assert result.resilience is not None
    assert result.resilience.degraded is False
    assert result.resilience.recovered is True
    assert [record.tool for record in result.resilience.tool_calls] == [
        ToolName.INTENT_CLASSIFIER,
        ToolName.POLICY_ANSWER,
    ]
    assert result.resilience.tool_calls[0].outcome is (ToolCallOutcome.RECOVERED)
    assert result.resilience.tool_calls[0].attempts == 2


@pytest.mark.asyncio
async def test_exhausted_classifier_failure_stops_downstream_tools() -> None:
    classifier = FailingClassifier(IntentType.POLICY_QUERY)
    policy_service = StaticPolicyService()
    unused = UnusedTool()
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=policy_service,
        material_checker=unused,
        approval_checker=unused,
        draft_generator=unused,
        tool_executor=_executor(),
    )

    result = await router.route("差旅住宿标准是多少？")

    assert result.status is AgentResponseStatus.UNAVAILABLE
    assert classifier.calls == 3
    assert policy_service.calls == 0
    assert "classifier-secret" not in result.reply
    assert result.workflow is not None
    assert result.workflow.terminal_node.value == "classify_intent"
    assert result.resilience is not None
    assert len(result.resilience.tool_calls) == 1
    assert result.resilience.tool_calls[0].tool is ToolName.INTENT_CLASSIFIER


@pytest.mark.asyncio
async def test_exhausted_policy_retries_return_safe_degradation() -> None:
    policy_service = FailingPolicyService()
    unused = UnusedTool()
    router = AgentRouter(
        intent_classifier=StaticClassifier(IntentType.POLICY_QUERY),
        policy_answer_service=policy_service,
        material_checker=unused,
        approval_checker=unused,
        draft_generator=unused,
        tool_executor=_executor(),
    )

    result = await router.route(
        "差旅住宿标准是多少？",
        session_id="day20-policy-failure",
    )

    assert result.status is AgentResponseStatus.UNAVAILABLE
    assert result.citations == ()
    assert policy_service.calls == 3
    assert "must-not-leak" not in result.reply
    assert result.memory is not None
    assert result.memory.stored_message_count == 2
    assert result.resilience is not None
    assert result.resilience.degraded is True
    failure = result.resilience.tool_calls[-1]
    assert failure.tool is ToolName.POLICY_ANSWER
    assert failure.attempts == 3
    assert failure.error is not None
    assert failure.error.category is ToolFailureCategory.UPSTREAM_UNAVAILABLE
    assert failure.error.error_id == "ERR-WORKFLOW0001"
    assert "must-not-leak" not in failure.error.user_message


@pytest.mark.asyncio
async def test_invalid_tool_output_fails_fast() -> None:
    material_checker = InvalidMaterialChecker()
    unused = UnusedTool()
    router = AgentRouter(
        intent_classifier=StaticClassifier(IntentType.MATERIAL_CHECK),
        policy_answer_service=StaticPolicyService(),
        material_checker=material_checker,
        approval_checker=unused,
        draft_generator=unused,
        tool_executor=_executor(),
    )

    result = await router.route("出差报销需要哪些材料？")

    assert result.status is AgentResponseStatus.UNAVAILABLE
    assert material_checker.calls == 1
    assert "must-not-leak" not in result.reply
    assert result.resilience is not None
    failure = result.resilience.tool_calls[-1]
    assert failure.attempts == 1
    assert failure.error is not None
    assert failure.error.category is ToolFailureCategory.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_failed_revision_preserves_last_valid_draft() -> None:
    material_checker, approval_checker, base_generator = _business_tools()
    generator = FailingRevisionGenerator(base_generator)
    router = AgentRouter(
        intent_classifier=DraftClassifier(),
        policy_answer_service=StaticPolicyService(),
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=generator,
        tool_executor=_executor(),
    )
    session_id = "day20-draft-preserved"
    created = await router.route(_COMPLETE_PURCHASE, session_id=session_id)
    assert created.application_draft is not None
    assert created.application_draft.draft is not None
    original_draft = created.application_draft.draft

    failed = await router.route(
        "把预计单价改为2200元",
        session_id=session_id,
    )

    assert failed.status is AgentResponseStatus.UNAVAILABLE
    assert generator.revision_calls == 3
    assert failed.application_draft is not None
    assert failed.application_draft.draft is not None
    assert failed.application_draft.draft.draft_id == original_draft.draft_id
    assert failed.application_draft.draft.revision == original_draft.revision
    assert failed.session is not None
    assert failed.session.phase is AgentSessionPhase.AWAITING_CONFIRMATION
    assert failed.resilience is not None
    assert failed.resilience.tool_calls[-1].tool is ToolName.DRAFT_REVISION


@pytest.mark.asyncio
async def test_submission_is_not_auto_retried_and_can_be_replayed_safely() -> None:
    material_checker, approval_checker, draft_generator = _business_tools()
    submitter = FailOnceSubmitter()
    router = AgentRouter(
        intent_classifier=DraftClassifier(),
        policy_answer_service=StaticPolicyService(),
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=draft_generator,
        submission_service=submitter,
        tool_executor=_executor(),
    )
    session_id = "day20-submission-retry"
    await router.route(_COMPLETE_PURCHASE, session_id=session_id)
    confirmed = await router.route("确认草稿", session_id=session_id)
    assert confirmed.status is AgentResponseStatus.CONFIRMED

    failed = await router.route("提交审批", session_id=session_id)

    assert failed.status is AgentResponseStatus.UNAVAILABLE
    assert submitter.calls == 1
    assert failed.application_draft is not None
    assert failed.application_draft.draft is not None
    assert failed.application_draft.draft.status is DraftStatus.CONFIRMED
    assert failed.resilience is not None
    record = failed.resilience.tool_calls[-1]
    assert record.tool is ToolName.APPROVAL_SUBMISSION
    assert record.attempts == 1
    assert record.max_attempts == 1
    assert record.retry_safe is False
    assert record.error is not None
    assert record.error.recovery_action is (ToolRecoveryAction.RESUBMIT_WITH_SAME_SESSION)

    submitted = await router.route("提交审批", session_id=session_id)

    assert submitter.calls == 2
    assert submitted.status is AgentResponseStatus.SUBMITTED
    assert submitted.submission is not None
    assert submitted.application_draft is not None
    assert submitted.application_draft.draft is not None
    assert submitted.application_draft.draft.status is DraftStatus.SUBMITTED
