from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.agent.intent import IntentType
from app.agent.router import (
    AgentResponseStatus,
    AgentWorkflowNode,
)
from app.tools.approval_models import (
    ApprovalAction,
    ApprovalApplicationType,
    ApprovalLevel,
    ApproverCode,
)
from app.tools.draft_models import (
    DraftFieldSource,
    DraftStatus,
    ValidationSeverity,
)
from app.tools.material_models import (
    ApplicationType,
    MaterialCheckMode,
)

_MessageText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=1000,
    ),
]


class AgentMessageRequest(BaseModel):
    """统一 Agent 入口的用户消息。"""

    message: _MessageText


class IntentClassificationResponse(BaseModel):
    """通过 API 返回的意图分类信息。"""

    intent: IntentType
    confidence: float
    reason: str


class MaterialRequirementResponse(BaseModel):
    """制度要求的一项材料。"""

    material_type: str
    display_name: str
    reason: str
    required_count: int
    sensitive: bool


class ProvidedMaterialResponse(BaseModel):
    """从当前用户消息识别出的已提供材料。"""

    material_type: str
    display_name: str
    provided_count: int


class MissingMaterialResponse(BaseModel):
    """材料比对后仍然缺少的项目。"""

    material_type: str
    display_name: str
    missing_count: int
    reason: str
    sensitive: bool


class MaterialCheckResponse(BaseModel):
    """材料工具的结构化检查明细。"""

    application_type: ApplicationType | None
    mode: MaterialCheckMode
    required_materials: list[MaterialRequirementResponse]
    provided_materials: list[ProvidedMaterialResponse]
    missing_materials: list[MissingMaterialResponse]
    materials_complete: bool | None
    clarification_question: str | None
    notes: list[str]


class ApprovalStepResponse(BaseModel):
    """审批路线中的一个有序节点。"""

    sequence: int
    approver: ApproverCode
    display_name: str
    action: ApprovalAction
    reason: str


class ApprovalCheckResponse(BaseModel):
    """审批工具的结构化规则判断结果。"""

    application_type: ApprovalApplicationType | None
    approval_level: ApprovalLevel | None
    amount: Decimal | None
    leave_days: Decimal | None
    steps: list[ApprovalStepResponse]
    special_conditions: list[str]
    clarification_question: str | None
    notes: list[str]


class DraftUserContextResponse(BaseModel):
    """由可信身份层注入的草稿申请人。"""

    employee_id: str
    employee_name: str
    department: str
    roles: list[str]
    region: str
    identity_source: str


class DraftFieldResponse(BaseModel):
    """草稿中一项已提取、计算或可信注入的字段。"""

    field_name: str
    display_name: str
    value: bool | int | Decimal | str
    source: DraftFieldSource
    sensitive: bool


class MissingDraftFieldResponse(BaseModel):
    """草稿仍缺少的一项必填字段。"""

    field_name: str
    display_name: str
    question: str


class DraftValidationIssueResponse(BaseModel):
    """草稿生成期间发现的一项校验问题。"""

    code: str
    severity: ValidationSeverity
    message: str
    blocking: bool


class DraftPolicySnapshotResponse(BaseModel):
    """生成草稿时使用的制度版本。"""

    document_id: str
    document_title: str
    version: str
    effective_date: date


class DraftAuditMetadataResponse(BaseModel):
    """草稿的最小审计与幂等信息。"""

    session_id: str
    request_id: str
    idempotency_key: str
    created_at: datetime
    created_by: str
    identity_source: str
    persisted: bool


class ApplicationDraftResponse(BaseModel):
    """供前端展示和后续确认的结构化申请草稿。"""

    draft_id: str
    application_type: ApplicationType
    title: str
    status: DraftStatus
    applicant: DraftUserContextResponse
    fields: list[DraftFieldResponse]
    missing_fields: list[MissingDraftFieldResponse]
    material_check: MaterialCheckResponse
    approval_check: ApprovalCheckResponse
    policy_snapshots: list[DraftPolicySnapshotResponse]
    validation_issues: list[DraftValidationIssueResponse]
    summary_lines: list[str]
    warnings: list[str]
    ready_for_confirmation: bool
    confirmation_required: bool
    user_confirmed: bool
    submitted: bool
    audit_metadata: DraftAuditMetadataResponse


class DraftGenerationResponse(BaseModel):
    """草稿工具的结构化生成结果。"""

    application_type: ApplicationType | None
    draft: ApplicationDraftResponse | None
    clarification_question: str | None


class AgentWorkflowStepResponse(BaseModel):
    """一次请求在 LangGraph 中实际执行的一个节点。"""

    sequence: int
    node: AgentWorkflowNode
    outcome: str


class AgentWorkflowTraceResponse(BaseModel):
    """供前端展示和排障的最小工作流轨迹。"""

    name: str
    version: str
    steps: list[AgentWorkflowStepResponse]
    terminal_node: AgentWorkflowNode


class AgentMessageResponse(BaseModel):
    """统一 Agent 入口的路由结果。"""

    request: str
    classification: IntentClassificationResponse
    status: AgentResponseStatus
    reply: str
    citations: list[str]
    material_check: MaterialCheckResponse | None = None
    approval_check: ApprovalCheckResponse | None = None
    application_draft: DraftGenerationResponse | None = None
    workflow: AgentWorkflowTraceResponse | None = None
