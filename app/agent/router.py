from __future__ import annotations

from app.agent.workflow import (
    AgentWorkflow,
    ApplicationDraftCreator,
    ApprovalChecker,
    IntentDetector,
    MaterialChecker,
    PolicyQuestionAnswerer,
)
from app.agent.workflow_models import (
    AgentResponseStatus,
    AgentRouteResult,
    AgentWorkflowNode,
    AgentWorkflowStep,
    AgentWorkflowTrace,
)

__all__ = [
    "AgentResponseStatus",
    "AgentRouteResult",
    "AgentRouter",
    "AgentWorkflowNode",
    "AgentWorkflowStep",
    "AgentWorkflowTrace",
]


class AgentRouter:
    """保留统一路由接口，并将实际编排委托给 LangGraph 工作流。"""

    def __init__(
        self,
        *,
        intent_classifier: IntentDetector,
        policy_answer_service: PolicyQuestionAnswerer,
        material_checker: MaterialChecker,
        approval_checker: ApprovalChecker,
        draft_generator: ApplicationDraftCreator,
    ) -> None:
        self._workflow = AgentWorkflow(
            intent_classifier=intent_classifier,
            policy_answer_service=policy_answer_service,
            material_checker=material_checker,
            approval_checker=approval_checker,
            draft_generator=draft_generator,
        )

    async def route(self, user_input: str) -> AgentRouteResult:
        """执行一次 LangGraph 工作流并返回结构化结果。"""

        return await self._workflow.run(user_input)

    def draw_workflow_mermaid(self) -> str:
        """返回编译后的 LangGraph 拓扑，便于本地验收。"""

        return self._workflow.draw_mermaid()
