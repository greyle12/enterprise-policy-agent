from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.agent.intent import IntentClassification, IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentRouter,
    AgentWorkflowNode,
)
from app.rag.policy_answer_service import PolicyAnswer
from app.rag.policy_context import PolicyCitation
from app.tools.approval_check import ApprovalRuleChecker
from app.tools.draft_generation import ApplicationDraftGenerator
from app.tools.draft_models import DraftUserContext
from app.tools.material_check import RequiredMaterialsChecker

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"

_COMPLETE_PURCHASE_DRAFT = (
    "帮我生成采购申请草稿，采购3台27英寸办公显示器，每台2000元，"
    "采购目的为给新员工配置办公设备，采购类别为IT设备，规格为27英寸2K，"
    "预算编号RD-2026，交付日期2026-08-15，使用地点苏州办公室，"
    "推荐供应商为苏州科技有限公司，推荐理由为历史合作交付稳定，普通采购，"
    "已准备技术需求说明、信息技术评审意见、产品规格说明和2家供应商报价。"
)

_ACTION_NODE_BY_INTENT = {
    IntentType.POLICY_QUERY: AgentWorkflowNode.ANSWER_POLICY,
    IntentType.MATERIAL_CHECK: AgentWorkflowNode.CHECK_MATERIALS,
    IntentType.APPROVAL_QUERY: AgentWorkflowNode.CHECK_APPROVAL,
    IntentType.DRAFT_GENERATION: AgentWorkflowNode.GENERATE_DRAFT,
    IntentType.UNKNOWN: AgentWorkflowNode.REQUEST_CLARIFICATION,
}

_CASES = (
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
        "采购一台办公电脑需要走什么审批？",
        IntentType.APPROVAL_QUERY,
        AgentResponseStatus.NEEDS_CLARIFICATION,
    ),
    (
        _COMPLETE_PURCHASE_DRAFT,
        IntentType.DRAFT_GENERATION,
        AgentResponseStatus.AWAITING_CONFIRMATION,
    ),
    (
        "给我讲一个笑话。",
        IntentType.UNKNOWN,
        AgentResponseStatus.NEEDS_CLARIFICATION,
    ),
)


class DeterministicIntentClassifier:
    """验收脚本使用的确定性分类器，不依赖外部 LLM。"""

    async def classify(self, user_input: str) -> IntentClassification:
        if "草稿" in user_input:
            intent = IntentType.DRAFT_GENERATION
        elif any(word in user_input for word in ("材料", "还缺什么")):
            intent = IntentType.MATERIAL_CHECK
        elif any(word in user_input for word in ("审批", "谁批")):
            intent = IntentType.APPROVAL_QUERY
        elif any(word in user_input for word in ("标准", "制度", "规定")):
            intent = IntentType.POLICY_QUERY
        else:
            intent = IntentType.UNKNOWN

        return IntentClassification(
            intent=intent,
            confidence=1.0,
            reason="确定性工作流验收分类。",
        )


class StubPolicyAnswerService:
    """仅用于验证制度问答分支被正确选中。"""

    async def answer(self, question: str) -> PolicyAnswer:
        return PolicyAnswer(
            question=question,
            answer="已进入制度问答节点并返回制度依据。[S1]",
            citations=(
                PolicyCitation(
                    source_id="S1",
                    chunk_id="workflow-policy-smoke",
                    document_title="差旅报销管理制度",
                    chapter_title="住宿标准",
                    article_label="第十条",
                    article_title="住宿费标准",
                    score=1.0,
                ),
            ),
        )


def _build_router() -> AgentRouter:
    material_checker = RequiredMaterialsChecker.from_policy_directory(_POLICY_DIRECTORY)
    approval_checker = ApprovalRuleChecker.from_policy_directory(_POLICY_DIRECTORY)
    draft_generator = ApplicationDraftGenerator.from_policy_directory(
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
        session_id="WORKFLOW-VERIFY",
    )
    return AgentRouter(
        intent_classifier=DeterministicIntentClassifier(),
        policy_answer_service=StubPolicyAnswerService(),
        material_checker=material_checker,
        approval_checker=approval_checker,
        draft_generator=draft_generator,
    )


async def _main() -> None:
    router = _build_router()
    mermaid = router.draw_workflow_mermaid()
    missing_nodes = [node.value for node in AgentWorkflowNode if node.value not in mermaid]
    if missing_nodes:
        raise RuntimeError("compiled workflow is missing nodes: " + ", ".join(missing_nodes))

    failures: list[str] = []
    for user_input, expected_intent, expected_status in _CASES:
        result = await router.route(user_input)
        expected_action_node = _ACTION_NODE_BY_INTENT[expected_intent]
        actual_nodes = (
            [step.node for step in result.workflow.steps] if result.workflow is not None else []
        )
        expected_nodes = [
            AgentWorkflowNode.RESOLVE_TURN,
            AgentWorkflowNode.CLASSIFY_INTENT,
            expected_action_node,
        ]
        if expected_status is AgentResponseStatus.AWAITING_CONFIRMATION:
            expected_nodes.append(AgentWorkflowNode.AWAIT_CONFIRMATION)
            expected_terminal_node = AgentWorkflowNode.AWAIT_CONFIRMATION
        else:
            expected_terminal_node = expected_action_node
        passed = (
            result.classification.intent is expected_intent
            and result.status is expected_status
            and actual_nodes == expected_nodes
            and result.workflow is not None
            and result.workflow.terminal_node is expected_terminal_node
        )

        print(
            json.dumps(
                {
                    "input": user_input,
                    "expected_intent": expected_intent,
                    "intent": result.classification.intent,
                    "expected_status": expected_status,
                    "status": result.status,
                    "workflow_nodes": actual_nodes,
                    "terminal_node": (
                        result.workflow.terminal_node if result.workflow is not None else None
                    ),
                    "passed": passed,
                },
                ensure_ascii=False,
            )
        )

        if not passed:
            failures.append(user_input)

    if failures:
        raise RuntimeError("Agent workflow verification failed: " + " | ".join(failures))


if __name__ == "__main__":
    asyncio.run(_main())
