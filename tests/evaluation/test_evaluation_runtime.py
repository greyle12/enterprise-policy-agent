from __future__ import annotations

import pytest

from app.agent.intent import IntentType
from app.evaluation.runtime import OfflineIntentClassifier


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("住宿标准是多少？", IntentType.POLICY_QUERY),
        ("报销需要哪些材料？", IntentType.MATERIAL_CHECK),
        ("采购要走什么审批？", IntentType.APPROVAL_QUERY),
        ("帮我生成采购申请草稿。", IntentType.DRAFT_GENERATION),
        ("给我推荐一部电影。", IntentType.UNKNOWN),
    ],
)
async def test_offline_classifier_is_deterministic(
    query: str,
    expected: IntentType,
) -> None:
    classifier = OfflineIntentClassifier()

    first = await classifier.classify(query)
    second = await classifier.classify(query)

    assert first == second
    assert first.intent is expected
    assert first.confidence == 1.0


async def test_offline_classifier_rejects_blank_input() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        await OfflineIntentClassifier().classify("   ")
