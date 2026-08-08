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
    AgentSessionInfo,
    AgentSessionPhase,
    AgentWorkflowNode,
    AgentWorkflowStep,
    AgentWorkflowTrace,
)

__all__ = [
    "AgentResponseStatus",
    "AgentRouteResult",
    "AgentRouter",
    "AgentSessionInfo",
    "AgentSessionPhase",
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

    async def route(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
    ) -> AgentRouteResult:
        """执行或恢复 LangGraph 会话并返回结构化结果。"""

        return await self._workflow.run(
            user_input,
            session_id=session_id,
        )

    async def clear_session(self, session_id: str) -> None:
        """清除一个演示会话的内存 checkpoint。"""

        await self._workflow.clear_session(session_id)

    def draw_workflow_mermaid(self) -> str:
        """返回编译后的 LangGraph 拓扑，便于本地验收。"""

        return self._workflow.draw_mermaid()
