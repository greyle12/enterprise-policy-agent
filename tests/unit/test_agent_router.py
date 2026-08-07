import asyncio
from decimal import Decimal

import pytest

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentRouter,
    AgentRouteResult,
    AgentWorkflowNode,
)
from app.rag.policy_answer_service import PolicyAnswer
from app.rag.policy_context import PolicyCitation
from app.tools.approval_models import (
    ApprovalAction,
    ApprovalApplicationType,
    ApprovalCheckAnswer,
    ApprovalCheckResult,
    ApprovalLevel,
    ApprovalStep,
    ApproverCode,
)
from app.tools.draft_models import (
    DraftGenerationAnswer,
    DraftGenerationResult,
)
from app.tools.material_models import (
    ApplicationType,
    MaterialCheckAnswer,
    MaterialCheckMode,
    MaterialCheckResult,
    MaterialRequirement,
)


class FakeIntentClassifier:
    def __init__(
        self,
        classification: IntentClassification,
    ) -> None:
        self.classification = classification
        self.calls: list[str] = []

    async def classify(
        self,
        user_input: str,
    ) -> IntentClassification:
        self.calls.append(user_input)
        return self.classification


class FakePolicyAnswerService:
    def __init__(self, answer: PolicyAnswer) -> None:
        self.answer_result = answer
        self.calls: list[str] = []

    async def answer(
        self,
        question: str,
    ) -> PolicyAnswer:
        self.calls.append(question)
        return self.answer_result


class FakeMaterialChecker:
    def __init__(self, answer: MaterialCheckAnswer) -> None:
        self.answer_result = answer
        self.calls: list[str] = []

    async def check(
        self,
        user_input: str,
    ) -> MaterialCheckAnswer:
        self.calls.append(user_input)
        return self.answer_result


class FakeApprovalChecker:
    def __init__(self, answer: ApprovalCheckAnswer) -> None:
        self.answer_result = answer
        self.calls: list[str] = []

    async def check(
        self,
        user_input: str,
    ) -> ApprovalCheckAnswer:
        self.calls.append(user_input)
        return self.answer_result


class FakeDraftGenerator:
    def __init__(self, answer: DraftGenerationAnswer) -> None:
        self.answer_result = answer
        self.calls: list[str] = []

    async def generate(
        self,
        user_input: str,
    ) -> DraftGenerationAnswer:
        self.calls.append(user_input)
        return self.answer_result


def _classification(
    intent: IntentType,
) -> IntentClassification:
    return IntentClassification(
        intent=intent,
        confidence=0.95,
        reason="测试分类理由",
    )


def _citation() -> PolicyCitation:
    return PolicyCitation(
        source_id="S1",
        chunk_id="travel-001",
        document_title="差旅报销制度",
        chapter_title="住宿标准",
        article_label="第十条",
        article_title="住宿费",
        score=0.98,
    )


def _material_answer(
    *,
    clarification_question: str | None = None,
) -> MaterialCheckAnswer:
    citation = PolicyCitation(
        source_id="S1",
        chunk_id="travel-materials-001",
        document_title="差旅报销管理制度",
        chapter_title="第六章 报销材料",
        article_label="第十六条",
        article_title="必备报销材料",
        score=1.0,
    )
    result = MaterialCheckResult(
        application_type=(
            ApplicationType.TRAVEL_REIMBURSEMENT
        ),
        mode=MaterialCheckMode.REQUIREMENTS,
        required_materials=(
            MaterialRequirement(
                material_type="travel_itinerary",
                display_name="差旅行程单",
                reason="制度要求",
            ),
        ),
        provided_materials=(),
        missing_materials=(),
        materials_complete=None,
        clarification_question=clarification_question,
        notes=(),
        citations=(citation,),
    )
    return MaterialCheckAnswer(
        request="出差报销需要哪些材料？",
        result=result,
        reply=(
            clarification_question
            or "需要准备差旅行程单。[S1]"
        ),
    )


def _approval_answer(
    *,
    clarification_question: str | None = None,
) -> ApprovalCheckAnswer:
    citation = PolicyCitation(
        source_id="S1",
        chunk_id="procurement-approval-001",
        document_title="采购管理办法",
        chapter_title="第四章 金额分级与审批",
        article_label="第十二条",
        article_title="一般采购审批",
        score=1.0,
    )
    result = ApprovalCheckResult(
        application_type=ApprovalApplicationType.PURCHASE,
        approval_level=(
            None
            if clarification_question is not None
            else ApprovalLevel.GENERAL_PURCHASE
        ),
        amount=(
            None
            if clarification_question is not None
            else Decimal(6000)
        ),
        leave_days=None,
        steps=(
            ()
            if clarification_question is not None
            else (
                ApprovalStep(
                    sequence=1,
                    approver=ApproverCode.DIRECT_MANAGER,
                    display_name="直属经理",
                    action=ApprovalAction.APPROVE,
                    reason="制度要求",
                ),
            )
        ),
        special_conditions=(),
        clarification_question=clarification_question,
        notes=(),
        citations=(citation,),
    )
    return ApprovalCheckAnswer(
        request="预计总金额6000元的采购需要谁审批？",
        result=result,
        reply=(
            clarification_question
            or "审批路线为直属经理。[S1]"
        ),
    )


def _draft_answer(
    *,
    clarification_question: str | None = None,
) -> DraftGenerationAnswer:
    citation = PolicyCitation(
        source_id="S1",
        chunk_id="purchase-draft-001",
        document_title="采购管理办法",
        chapter_title="第三章 采购申请",
        article_label="第九条",
        article_title="采购申请必填信息",
        score=1.0,
    )
    result = DraftGenerationResult(
        application_type=ApplicationType.PURCHASE,
        draft=None,
        clarification_question=clarification_question,
        citations=(citation,),
    )
    return DraftGenerationAnswer(
        request="帮我生成采购申请草稿。",
        result=result,
        reply=(
            clarification_question
            or "已生成采购申请草稿。[S1]"
        ),
    )


def _assert_workflow_path(
    result: AgentRouteResult,
    action_node: AgentWorkflowNode,
) -> None:
    assert result.workflow is not None
    assert result.workflow.name == "enterprise_policy_workflow"
    assert result.workflow.version == "1.0"
    assert [step.sequence for step in result.workflow.steps] == [1, 2]
    assert [step.node for step in result.workflow.steps] == [
        AgentWorkflowNode.CLASSIFY_INTENT,
        action_node,
    ]
    assert result.workflow.terminal_node is action_node
    assert result.workflow.steps[-1].outcome == result.status.value


def test_routes_policy_query_to_answer_service() -> None:
    classifier = FakeIntentClassifier(
        _classification(IntentType.POLICY_QUERY)
    )
    citation = _citation()
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="出差住宿标准是多少？",
            answer="普通员工住宿标准为500元。[S1]",
            citations=(citation,),
        )
    )
    material_checker = FakeMaterialChecker(_material_answer())
    approval_checker = FakeApprovalChecker(_approval_answer())
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=FakeDraftGenerator(_draft_answer()),
    )

    result = asyncio.run(
        router.route("  出差住宿标准是多少？  ")
    )

    assert result.request == "出差住宿标准是多少？"
    assert result.status is AgentResponseStatus.COMPLETED
    assert result.reply == "普通员工住宿标准为500元。[S1]"
    assert result.citations == (citation,)
    assert classifier.calls == ["出差住宿标准是多少？"]
    assert answer_service.calls == ["出差住宿标准是多少？"]
    assert material_checker.calls == []
    assert approval_checker.calls == []
    _assert_workflow_path(
        result,
        AgentWorkflowNode.ANSWER_POLICY,
    )


def test_routes_material_check_to_material_tool() -> None:
    classifier = FakeIntentClassifier(
        _classification(IntentType.MATERIAL_CHECK)
    )
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="不应调用",
            answer="不应调用",
            citations=(),
        )
    )
    material_answer = _material_answer()
    material_checker = FakeMaterialChecker(material_answer)
    approval_checker = FakeApprovalChecker(_approval_answer())
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=FakeDraftGenerator(_draft_answer()),
    )

    result = asyncio.run(
        router.route("  出差报销需要哪些材料？  ")
    )

    assert result.status is AgentResponseStatus.COMPLETED
    assert result.reply == material_answer.reply
    assert result.material_check == material_answer.result
    assert result.citations == material_answer.result.citations
    assert material_checker.calls == ["出差报销需要哪些材料？"]
    assert answer_service.calls == []
    assert approval_checker.calls == []
    _assert_workflow_path(
        result,
        AgentWorkflowNode.CHECK_MATERIALS,
    )


def test_material_check_can_request_clarification() -> None:
    classifier = FakeIntentClassifier(
        _classification(IntentType.MATERIAL_CHECK)
    )
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="不应调用",
            answer="不应调用",
            citations=(),
        )
    )
    question = "请说明是差旅报销还是普通费用报销。"
    material_checker = FakeMaterialChecker(
        _material_answer(clarification_question=question)
    )
    approval_checker = FakeApprovalChecker(_approval_answer())
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=FakeDraftGenerator(_draft_answer()),
    )

    result = asyncio.run(router.route("报销需要哪些材料？"))

    assert (
        result.status
        is AgentResponseStatus.NEEDS_CLARIFICATION
    )
    assert result.reply == question
    assert result.material_check is not None
    assert answer_service.calls == []
    assert approval_checker.calls == []
    _assert_workflow_path(
        result,
        AgentWorkflowNode.CHECK_MATERIALS,
    )


def test_routes_approval_query_to_approval_tool() -> None:
    classifier = FakeIntentClassifier(
        _classification(IntentType.APPROVAL_QUERY)
    )
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="不应调用",
            answer="不应调用",
            citations=(),
        )
    )
    material_checker = FakeMaterialChecker(_material_answer())
    approval_answer = _approval_answer()
    approval_checker = FakeApprovalChecker(approval_answer)
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=FakeDraftGenerator(_draft_answer()),
    )

    user_input = "预计总金额6000元的采购需要谁审批？"
    result = asyncio.run(router.route(f"  {user_input}  "))

    assert result.status is AgentResponseStatus.COMPLETED
    assert result.reply == approval_answer.reply
    assert result.approval_check == approval_answer.result
    assert result.citations == approval_answer.result.citations
    assert approval_checker.calls == [user_input]
    assert answer_service.calls == []
    assert material_checker.calls == []
    _assert_workflow_path(
        result,
        AgentWorkflowNode.CHECK_APPROVAL,
    )


def test_approval_check_can_request_clarification() -> None:
    classifier = FakeIntentClassifier(
        _classification(IntentType.APPROVAL_QUERY)
    )
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="不应调用",
            answer="不应调用",
            citations=(),
        )
    )
    material_checker = FakeMaterialChecker(_material_answer())
    question = "请补充预计采购总金额。"
    approval_checker = FakeApprovalChecker(
        _approval_answer(clarification_question=question)
    )
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=FakeDraftGenerator(_draft_answer()),
    )

    result = asyncio.run(router.route("采购电脑需要谁审批？"))

    assert (
        result.status
        is AgentResponseStatus.NEEDS_CLARIFICATION
    )
    assert result.reply == question
    assert result.approval_check is not None
    assert answer_service.calls == []
    assert material_checker.calls == []
    _assert_workflow_path(
        result,
        AgentWorkflowNode.CHECK_APPROVAL,
    )


def test_routes_draft_generation_to_draft_tool() -> None:
    classifier = FakeIntentClassifier(
        _classification(IntentType.DRAFT_GENERATION)
    )
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="不应调用",
            answer="不应调用",
            citations=(),
        )
    )
    material_checker = FakeMaterialChecker(_material_answer())
    approval_checker = FakeApprovalChecker(_approval_answer())
    draft_answer = _draft_answer()
    draft_generator = FakeDraftGenerator(draft_answer)
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=draft_generator,
    )

    user_input = "帮我生成采购申请草稿。"
    result = asyncio.run(router.route(f"  {user_input}  "))

    assert result.status is AgentResponseStatus.COMPLETED
    assert result.reply == draft_answer.reply
    assert result.application_draft == draft_answer.result
    assert result.citations == draft_answer.result.citations
    assert draft_generator.calls == [user_input]
    assert answer_service.calls == []
    assert material_checker.calls == []
    assert approval_checker.calls == []
    _assert_workflow_path(
        result,
        AgentWorkflowNode.GENERATE_DRAFT,
    )


def test_draft_generation_can_request_clarification() -> None:
    classifier = FakeIntentClassifier(
        _classification(IntentType.DRAFT_GENERATION)
    )
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="不应调用",
            answer="不应调用",
            citations=(),
        )
    )
    material_checker = FakeMaterialChecker(_material_answer())
    approval_checker = FakeApprovalChecker(_approval_answer())
    question = "请补充采购事项、数量和预计单价。"
    draft_generator = FakeDraftGenerator(
        _draft_answer(clarification_question=question)
    )
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=draft_generator,
    )

    result = asyncio.run(router.route("帮我生成采购申请草稿。"))

    assert (
        result.status
        is AgentResponseStatus.NEEDS_CLARIFICATION
    )
    assert result.reply == question
    assert result.application_draft is not None
    assert draft_generator.calls == ["帮我生成采购申请草稿。"]
    assert answer_service.calls == []
    assert material_checker.calls == []
    assert approval_checker.calls == []
    _assert_workflow_path(
        result,
        AgentWorkflowNode.GENERATE_DRAFT,
    )


def test_unknown_intent_requests_clarification() -> None:
    classifier = FakeIntentClassifier(
        _classification(IntentType.UNKNOWN)
    )
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="不应调用",
            answer="不应调用",
            citations=(),
        )
    )
    material_checker = FakeMaterialChecker(_material_answer())
    approval_checker = FakeApprovalChecker(_approval_answer())
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=FakeDraftGenerator(_draft_answer()),
    )

    result = asyncio.run(
        router.route("帮我看看这个")
    )

    assert (
        result.status
        is AgentResponseStatus.NEEDS_CLARIFICATION
    )
    assert "请补充" in result.reply
    assert result.citations == ()
    assert answer_service.calls == []
    assert material_checker.calls == []
    assert approval_checker.calls == []
    _assert_workflow_path(
        result,
        AgentWorkflowNode.REQUEST_CLARIFICATION,
    )


def test_compiles_expected_langgraph_topology() -> None:
    router = AgentRouter(
        intent_classifier=FakeIntentClassifier(
            _classification(IntentType.UNKNOWN)
        ),
        policy_answer_service=FakePolicyAnswerService(
            PolicyAnswer(
                question="不应调用",
                answer="不应调用",
                citations=(),
            )
        ),
        material_checker=FakeMaterialChecker(_material_answer()),
        approval_checker=FakeApprovalChecker(_approval_answer()),
        draft_generator=FakeDraftGenerator(_draft_answer()),
    )

    mermaid = router.draw_workflow_mermaid()

    for node in AgentWorkflowNode:
        assert node.value in mermaid
    assert "__start__" in mermaid
    assert "__end__" in mermaid


@pytest.mark.parametrize("user_input", ["", "   ", "\n"])
def test_rejects_blank_input_before_classification(
    user_input: str,
) -> None:
    classifier = FakeIntentClassifier(
        _classification(IntentType.POLICY_QUERY)
    )
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="不应调用",
            answer="不应调用",
            citations=(),
        )
    )
    material_checker = FakeMaterialChecker(_material_answer())
    approval_checker = FakeApprovalChecker(_approval_answer())
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=FakeDraftGenerator(_draft_answer()),
    )

    with pytest.raises(
        ValueError,
        match="user_input must not be blank",
    ):
        asyncio.run(router.route(user_input))

    assert classifier.calls == []
    assert answer_service.calls == []
    assert material_checker.calls == []
    assert approval_checker.calls == []
