from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.agent.intent import IntentType
from app.agent.intent_classifier import IntentClassifier
from app.agent.router import AgentResponseStatus, AgentRouter
from app.core.config import get_settings
from app.llm.openai_compatible_client import (
    OpenAICompatibleLLMClient,
)
from app.rag.embeddings import BGEEmbeddingProvider
from app.rag.policy_answer_service import PolicyAnswerService
from app.rag.policy_retriever import PolicyRetriever
from app.tools.material_check import RequiredMaterialsChecker

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"

_SMOKE_CASES = (
    (
        "出差住宿标准是多少？",
        IntentType.POLICY_QUERY,
        AgentResponseStatus.COMPLETED,
    ),
    (
        "出差报销需要准备哪些材料？",
        IntentType.MATERIAL_CHECK,
        AgentResponseStatus.COMPLETED,
    ),
    (
        "采购一台办公电脑需要走什么审批？",
        IntentType.APPROVAL_QUERY,
        AgentResponseStatus.UNAVAILABLE,
    ),
    (
        "帮我生成一份采购申请草稿。",
        IntentType.DRAFT_GENERATION,
        AgentResponseStatus.UNAVAILABLE,
    ),
    (
        "给我讲一个笑话。",
        IntentType.UNKNOWN,
        AgentResponseStatus.NEEDS_CLARIFICATION,
    ),
)


async def _main() -> None:
    embedding_provider = BGEEmbeddingProvider(
        model_name="BAAI/bge-small-zh-v1.5",
    )
    retriever = PolicyRetriever.from_directory(
        _POLICY_DIRECTORY,
        embedding_provider=embedding_provider,
    )
    client = OpenAICompatibleLLMClient.from_settings(
        get_settings()
    )
    router = AgentRouter(
        intent_classifier=IntentClassifier(
            llm_client=client,
        ),
        policy_answer_service=PolicyAnswerService(
            retriever=retriever,
            llm_client=client,
        ),
        material_checker=(
            RequiredMaterialsChecker.from_policy_directory(
                _POLICY_DIRECTORY
            )
        ),
    )
    failures: list[str] = []

    try:
        for user_input, expected_intent, expected_status in (
            _SMOKE_CASES
        ):
            result = await router.route(user_input)
            has_expected_citations = (
                bool(result.citations)
                if expected_status is AgentResponseStatus.COMPLETED
                else not result.citations
            )
            passed = (
                result.classification.intent is expected_intent
                and result.status is expected_status
                and has_expected_citations
                and (
                    result.material_check is not None
                    if expected_intent is IntentType.MATERIAL_CHECK
                    else result.material_check is None
                )
            )

            print(
                json.dumps(
                    {
                        "input": user_input,
                        "expected_intent": expected_intent,
                        "expected_status": expected_status,
                        "intent": result.classification.intent,
                        "confidence": (
                            result.classification.confidence
                        ),
                        "status": result.status,
                        "reply": result.reply,
                        "citations": [
                            citation.source_id
                            for citation in result.citations
                        ],
                        "material_check": (
                            {
                                "application_type": (
                                    result.material_check.application_type
                                ),
                                "mode": result.material_check.mode,
                                "required_count": len(
                                    result.material_check.required_materials
                                ),
                                "missing_count": len(
                                    result.material_check.missing_materials
                                ),
                                "materials_complete": (
                                    result.material_check.materials_complete
                                ),
                            }
                            if result.material_check is not None
                            else None
                        ),
                        "passed": passed,
                    },
                    ensure_ascii=False,
                )
            )

            if not passed:
                failures.append(
                    f"{user_input}: expected "
                    f"{expected_intent}/{expected_status}, got "
                    f"{result.classification.intent}/"
                    f"{result.status}"
                )
    finally:
        await client.close()

    if failures:
        failure_text = "\n".join(failures)
        raise RuntimeError(
            "Agent router smoke test failed:\n"
            f"{failure_text}"
        )


if __name__ == "__main__":
    asyncio.run(_main())
