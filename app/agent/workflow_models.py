from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from operator import add
from typing import Annotated, TypedDict

from app.agent.intent import IntentClassification
from app.rag.policy_context import PolicyCitation
from app.tools.approval_models import ApprovalCheckResult
from app.tools.draft_models import DraftGenerationResult
from app.tools.material_models import MaterialCheckResult


class AgentResponseStatus(StrEnum):
    """统一 Agent 入口可能返回的处理状态。"""

    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNAVAILABLE = "unavailable"


class AgentWorkflowNode(StrEnum):
    """LangGraph 工作流中可观测的节点名称。"""

    CLASSIFY_INTENT = "classify_intent"
    ANSWER_POLICY = "answer_policy"
    CHECK_MATERIALS = "check_materials"
    CHECK_APPROVAL = "check_approval"
    GENERATE_DRAFT = "generate_draft"
    REQUEST_CLARIFICATION = "request_clarification"


@dataclass(frozen=True, slots=True)
class AgentWorkflowStep:
    """一次工作流执行实际经过的节点及其结果。"""

    sequence: int
    node: AgentWorkflowNode
    outcome: str


@dataclass(frozen=True, slots=True)
class AgentWorkflowTrace:
    """返回给调用方的最小工作流可观测信息。"""

    name: str
    version: str
    steps: tuple[AgentWorkflowStep, ...]
    terminal_node: AgentWorkflowNode


@dataclass(frozen=True, slots=True)
class AgentRouteResult:
    """统一 Agent 工作流的一次结构化结果。"""

    request: str
    classification: IntentClassification
    status: AgentResponseStatus
    reply: str
    citations: tuple[PolicyCitation, ...] = ()
    material_check: MaterialCheckResult | None = None
    approval_check: ApprovalCheckResult | None = None
    application_draft: DraftGenerationResult | None = None
    workflow: AgentWorkflowTrace | None = None


class AgentWorkflowState(TypedDict, total=False):
    """节点之间传递的共享状态；业务结果始终保留结构化类型。"""

    request: str
    classification: IntentClassification
    status: AgentResponseStatus
    reply: str
    citations: tuple[PolicyCitation, ...]
    material_check: MaterialCheckResult
    approval_check: ApprovalCheckResult
    application_draft: DraftGenerationResult
    trace_steps: Annotated[tuple[AgentWorkflowStep, ...], add]
