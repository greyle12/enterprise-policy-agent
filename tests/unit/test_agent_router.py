import asyncio

import pytest

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentRouter,
)
from app.rag.policy_answer_service import PolicyAnswer
from app.rag.policy_context import PolicyCitation


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
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
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


@pytest.mark.parametrize(
    ("intent", "expected_reply_fragment"),
    [
        (IntentType.MATERIAL_CHECK, "材料检查"),
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
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
    )

    result = asyncio.run(
        router.route("帮我处理这个请求")
    )

    assert result.status is AgentResponseStatus.UNAVAILABLE
    assert expected_reply_fragment in result.reply
    assert result.citations == ()
    assert answer_service.calls == []


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
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
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
    router = AgentRouter(
        intent_classifier=classifier,
        policy_answer_service=answer_service,
    )

    with pytest.raises(
        ValueError,
        match="user_input must not be blank",
    ):
        asyncio.run(router.route(user_input))

    assert classifier.calls == []
    assert answer_service.calls == []
