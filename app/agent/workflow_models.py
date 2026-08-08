from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict

from app.agent.intent import IntentClassification
from app.rag.policy_context import PolicyCitation
from app.tools.approval_models import ApprovalCheckResult
from app.tools.draft_models import DraftGenerationResult
from app.tools.material_models import MaterialCheckResult


class AgentResponseStatus(StrEnum):
    """统一 Agent 入口可能返回的处理状态。"""

    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"


class AgentSessionPhase(StrEnum):
    """会话中当前业务办理所处的阶段。"""

    IDLE = "idle"
    COLLECTING_INFORMATION = "collecting_information"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class AgentTurnAction(StrEnum):
    """在调用通用意图分类器前确定的会话级动作。"""

    NEW_REQUEST = "new_request"
    UPDATE_DRAFT = "update_draft"
    CONFIRM_DRAFT = "confirm_draft"
    CANCEL_DRAFT = "cancel_draft"


class AgentWorkflowNode(StrEnum):
    """LangGraph 工作流中可观测的节点名称。"""

    RESOLVE_TURN = "resolve_turn"
    CLASSIFY_INTENT = "classify_intent"
    ANSWER_POLICY = "answer_policy"
    CHECK_MATERIALS = "check_materials"
    CHECK_APPROVAL = "check_approval"
    GENERATE_DRAFT = "generate_draft"
    UPDATE_DRAFT = "update_draft"
    AWAIT_CONFIRMATION = "await_confirmation"
    HUMAN_CONFIRMATION_GATE = "human_confirmation_gate"
    CONFIRM_DRAFT = "confirm_draft"
    CANCEL_DRAFT = "cancel_draft"
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
    interrupted: bool = False


@dataclass(frozen=True, slots=True)
class AgentSessionInfo:
    """API 调用方继续多轮办理所需的会话信息。"""

    session_id: str
    turn_number: int
    phase: AgentSessionPhase
    active_draft_id: str | None
    draft_revision: int | None
    pending_confirmation: bool
    checkpoint_backend: str = "in_memory"
    survives_process_restart: bool = False


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
    session: AgentSessionInfo | None = None


class AgentWorkflowState(TypedDict, total=False):
    """节点之间及 checkpoint 中传递的结构化会话状态。"""

    session_id: str
    turn_number: int
    session_phase: AgentSessionPhase
    turn_action: AgentTurnAction
    request: str
    classification: IntentClassification | None
    status: AgentResponseStatus | None
    reply: str | None
    citations: tuple[PolicyCitation, ...]
    material_check: MaterialCheckResult | None
    approval_check: ApprovalCheckResult | None
    application_draft: DraftGenerationResult | None
    active_draft: DraftGenerationResult | None
    draft_messages: tuple[str, ...]
    trace_steps: tuple[AgentWorkflowStep, ...]
