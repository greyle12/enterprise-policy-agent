from __future__ import annotations

import asyncio
import json

from app.agent.intent import IntentType
from app.agent.intent_classifier import IntentClassifier
from app.core.config import get_settings
from app.llm.openai_compatible_client import (
    OpenAICompatibleLLMClient,
)

_SMOKE_CASES = (
    (
        "出差住宿标准是多少？",
        IntentType.POLICY_QUERY,
    ),
    (
        "出差报销需要准备哪些材料？",
        IntentType.MATERIAL_CHECK,
    ),
    (
        "采购一台办公电脑需要走什么审批？",
        IntentType.APPROVAL_QUERY,
    ),
    (
        "帮我生成一份采购申请草稿。",
        IntentType.DRAFT_GENERATION,
    ),
    (
        "给我讲一个笑话。",
        IntentType.UNKNOWN,
    ),
)


async def _main() -> None:
    client = OpenAICompatibleLLMClient.from_settings(get_settings())
    classifier = IntentClassifier(llm_client=client)
    failures: list[str] = []

    try:
        for user_input, expected_intent in _SMOKE_CASES:
            result = await classifier.classify(user_input)
            passed = result.intent is expected_intent

            print(
                json.dumps(
                    {
                        "input": user_input,
                        "expected_intent": expected_intent,
                        **result.model_dump(mode="json"),
                        "passed": passed,
                    },
                    ensure_ascii=False,
                )
            )

            if not passed:
                failures.append(f"{user_input}: expected {expected_intent}, got {result.intent}")
    finally:
        await client.close()

    if failures:
        failure_text = "\n".join(failures)
        raise RuntimeError(f"Intent classifier smoke test failed:\n{failure_text}")


if __name__ == "__main__":
    asyncio.run(_main())
