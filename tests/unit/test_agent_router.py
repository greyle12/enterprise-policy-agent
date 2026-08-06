import asyncio

import pytest

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentRouter,
)
from app.rag.policy_answer_service import PolicyAnswer
from app.rag.policy_context import PolicyCitation
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
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
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
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
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
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
    )

    result = asyncio.run(router.route("报销需要哪些材料？"))

    assert (
        result.status
        is AgentResponseStatus.NEEDS_CLARIFICATION
    )
    assert result.reply == question
    assert result.material_check is not None
    assert answer_service.calls == []


@pytest.mark.parametrize(
    ("intent", "expected_reply_fragment"),
    [
        (IntentType.APPROVAL_QUERY, "审批流程"),
        (IntentType.DRAFT_GENERATION, "申请草稿"),
    ],
)
def test_known_intent_without_tool_returns_unavailable(
    intent: IntentType,
    expected_reply_fragment: str,
) -> None:
    classifier = FakeIntentClassifier(
        _classification(intent)
    )
    answer_service = FakePolicyAnswerService(
        PolicyAnswer(
            question="不应调用",
            answer="不应调用",
            citations=(),
        )
    )
    material_checker = FakeMaterialChecker(_material_answer())
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
    )

    result = asyncio.run(
        router.route("帮我处理这个请求")
    )

    assert result.status is AgentResponseStatus.UNAVAILABLE
    assert expected_reply_fragment in result.reply
    assert result.citations == ()
    assert answer_service.calls == []
    assert material_checker.calls == []


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
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
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
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
        material_checker=material_checker,
    )

    with pytest.raises(
        ValueError,
        match="user_input must not be blank",
    ):
        asyncio.run(router.route(user_input))

    assert classifier.calls == []
    assert answer_service.calls == []
    assert material_checker.calls == []
