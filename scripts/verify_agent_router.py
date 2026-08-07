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
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftUserContext
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
        "采购三台办公显示器，每台2000元，需要走什么审批？",
        IntentType.APPROVAL_QUERY,
        AgentResponseStatus.COMPLETED,
    ),
    (
        (
            "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
            "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
            "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
            "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
            "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
        ),
        IntentType.DRAFT_GENERATION,
        AgentResponseStatus.COMPLETED,
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
    material_checker = (
        RequiredMaterialsChecker.from_policy_directory(
            _POLICY_DIRECTORY
        )
    )
    approval_checker = (
        ApprovalRuleChecker.from_policy_directory(
            _POLICY_DIRECTORY
        )
    )
    router = AgentRouter(
        intent_classifier=IntentClassifier(
            llm_client=client,
        ),
        policy_answer_service=PolicyAnswerService(
            retriever=retriever,
            llm_client=client,
        ),
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=(
            ApplicationDraftGenerator.from_policy_directory(
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
                and (
                    result.approval_check is not None
                    if expected_intent is IntentType.APPROVAL_QUERY
                    else result.approval_check is None
                )
                and (
                    result.application_draft is not None
                    if expected_intent is IntentType.DRAFT_GENERATION
                    else result.application_draft is None
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
                        "approval_check": (
                            {
                                "application_type": (
                                    result.approval_check.application_type
                                ),
                                "approval_level": (
                                    result.approval_check.approval_level
                                ),
                                "amount": str(
                                    result.approval_check.amount
                                ),
                                "approvers": [
                                    step.approver
                                    for step in result.approval_check.steps
                                ],
                            }
                            if result.approval_check is not None
                            else None
                        ),
                        "application_draft": (
                            {
                                "application_type": (
                                    result.application_draft.application_type
                                ),
                                "draft_status": (
                                    result.application_draft.draft.status
                                    if result.application_draft.draft is not None
                                    else None
                                ),
                                "ready_for_confirmation": (
                                    result.application_draft.draft.ready_for_confirmation
                                    if result.application_draft.draft is not None
                                    else False
                                ),
                            }
                            if result.application_draft is not None
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
