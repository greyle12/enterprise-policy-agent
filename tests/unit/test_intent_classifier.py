import asyncio
from collections.abc import Sequence

import pytest

from app.agent.intent import IntentType
from app.agent.intent_classifier import IntentClassifier
from app.llm.client import ChatMessage


class FakeLLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[ChatMessage]] = []

    async def chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> str:
        self.calls.append(list(messages))
        return self.response


@pytest.mark.parametrize(
    ("response", "expected_intent"),
    [
        (
            ('{"intent":"policy_query","confidence":0.96,"reason":"询问住宿制度标准"}'),
            IntentType.POLICY_QUERY,
        ),
        (
            ('{"intent":"material_check","confidence":0.95,"reason":"询问报销所需材料"}'),
            IntentType.MATERIAL_CHECK,
        ),
        (
            ('{"intent":"approval_query","confidence":0.94,"reason":"询问采购审批路径"}'),
            IntentType.APPROVAL_QUERY,
        ),
        (
            ('{"intent":"draft_generation","confidence":0.93,"reason":"要求生成采购申请草稿"}'),
            IntentType.DRAFT_GENERATION,
        ),
        (
            ('{"intent":"unknown","confidence":0.91,"reason":"与企业制度无关"}'),
            IntentType.UNKNOWN,
        ),
    ],
)
def test_parses_supported_intents(
    response: str,
    expected_intent: IntentType,
) -> None:
    llm_client = FakeLLMClient(response)
    classifier = IntentClassifier(
        llm_client=llm_client,
    )

    result = asyncio.run(classifier.classify("测试请求"))

    assert result.intent is expected_intent
    assert 0.0 <= result.confidence <= 1.0
    assert result.reason


def test_builds_classification_messages_with_trimmed_input() -> None:
    llm_client = FakeLLMClient('{"intent":"policy_query","confidence":0.98,"reason":"查询制度"}')
    classifier = IntentClassifier(
        llm_client=llm_client,
    )

    asyncio.run(classifier.classify("  出差住宿标准是多少？  "))

    assert len(llm_client.calls) == 1
    system_message, user_message = llm_client.calls[0]

    assert system_message["role"] == "system"
    assert "material_check" in system_message["content"]
    assert "approval_query" in system_message["content"]
    assert "draft_generation" in system_message["content"]
    assert user_message == {
        "role": "user",
        "content": ('请分类下面 JSON 中 request 字段的文本：\n{"request": "出差住宿标准是多少？"}'),
    }


def test_accepts_json_wrapped_in_markdown_fence() -> None:
    llm_client = FakeLLMClient(
        """```json
{"intent":"material_check","confidence":0.90,"reason":"检查材料"}
```"""
    )
    classifier = IntentClassifier(
        llm_client=llm_client,
    )

    result = asyncio.run(classifier.classify("报销材料齐全吗？"))

    assert result.intent is IntentType.MATERIAL_CHECK


@pytest.mark.parametrize(
    "response",
    [
        "",
        "不是 JSON",
        ('{"intent":"unsupported","confidence":0.9,"reason":"未知枚举"}'),
        '{"intent":"policy_query","confidence":0.9}',
        ('{"intent":"policy_query","confidence":1.1,"reason":"置信度越界"}'),
        ('{"intent":"policy_query","confidence":"0.9","reason":"置信度不是数字"}'),
        ('{"intent":"policy_query","confidence":0.9,"reason":"包含额外字段","extra":true}'),
    ],
)
def test_falls_back_to_unknown_for_invalid_output(
    response: str,
) -> None:
    classifier = IntentClassifier(
        llm_client=FakeLLMClient(response),
    )

    result = asyncio.run(classifier.classify("帮我处理一下"))

    assert result.intent is IntentType.UNKNOWN
    assert result.confidence == 0.0
    assert "无法解析" in result.reason


def test_falls_back_to_unknown_below_confidence_threshold() -> None:
    classifier = IntentClassifier(
        llm_client=FakeLLMClient(
            '{"intent":"approval_query","confidence":0.59,"reason":"可能在询问审批"}'
        ),
    )

    result = asyncio.run(classifier.classify("这个要怎么处理？"))

    assert result.intent is IntentType.UNKNOWN
    assert result.confidence == 0.59
    assert "低于阈值" in result.reason


@pytest.mark.parametrize(
    "workflow_only_intent",
    [
        "draft_update",
        "draft_confirmation",
        "draft_cancellation",
    ],
)
def test_llm_cannot_select_workflow_only_intent(
    workflow_only_intent: str,
) -> None:
    classifier = IntentClassifier(
        llm_client=FakeLLMClient(
            f'{{"intent":"{workflow_only_intent}","confidence":1.0,"reason":"尝试越过会话状态机"}}'
        )
    )

    result = asyncio.run(classifier.classify("确认草稿"))

    assert result.intent is IntentType.UNKNOWN
    assert "会话工作流" in result.reason


@pytest.mark.parametrize(
    "user_input",
    ["", "   ", "\n"],
)
def test_rejects_blank_user_input(user_input: str) -> None:
    llm_client = FakeLLMClient('{"intent":"unknown","confidence":1.0,"reason":"不应调用"}')
    classifier = IntentClassifier(
        llm_client=llm_client,
    )

    with pytest.raises(
        ValueError,
        match="user_input must not be blank",
    ):
        asyncio.run(classifier.classify(user_input))

    assert llm_client.calls == []


@pytest.mark.parametrize(
    "min_confidence",
    [-0.01, 1.01],
)
def test_rejects_invalid_confidence_threshold(
    min_confidence: float,
) -> None:
    with pytest.raises(
        ValueError,
        match="between zero and one",
    ):
        IntentClassifier(
            llm_client=FakeLLMClient(""),
            min_confidence=min_confidence,
        )
