from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    TypeAdapter,
)

from app.agent.intent import IntentType
from app.tools.approval_models import (
    ApprovalApplicationType,
    ApprovalLevel,
    ApproverCode,
)
from app.tools.material_models import (
    ApplicationType,
    MaterialCheckMode,
)

CaseId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[A-Z]+-[0-9]{3}$",
    ),
]
QueryText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=500,
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )


class EvaluationMode(StrEnum):
    """评测运行时使用的意图识别方式。"""

    OFFLINE = "offline"
    LIVE = "live"


class GoldenCaseCategory(StrEnum):
    """黄金用例执行的主要业务组件。"""

    ROUTING = "routing"
    MATERIAL_CHECK = "material_check"
    APPROVAL_ROUTE = "approval_route"


class EvaluationMetric(StrEnum):
    """Day 16 质量门禁统计的五项核心指标。"""

    INTENT_ACCURACY = "intent_accuracy"
    TOOL_SELECTION_ACCURACY = "tool_selection_accuracy"
    MATERIAL_CHECK_ACCURACY = "material_check_accuracy"
    APPROVAL_ROUTE_ACCURACY = "approval_route_accuracy"
    CITATION_ACCURACY = "citation_accuracy"


class EvaluationTool(StrEnum):
    """从工作流轨迹中观测到的业务工具。"""

    SEARCH_POLICY = "search_policy"
    CHECK_REQUIRED_MATERIALS = "check_required_materials"
    CHECK_APPROVAL_ROUTE = "check_approval_route"
    CREATE_APPLICATION_DRAFT = "create_application_draft"
    NONE = "none"


class ExpectedCitation(_StrictModel):
    """黄金用例要求命中的制度名称和条款。"""

    document_title: str = Field(min_length=1, max_length=100)
    article_label: str = Field(min_length=1, max_length=30)


class GoldenRoutingCase(_StrictModel):
    """意图识别与工具路由用例。"""

    case_id: CaseId
    category: Literal[GoldenCaseCategory.ROUTING]
    title: str = Field(min_length=1, max_length=100)
    query: QueryText
    expected_intent: IntentType
    expected_tool: EvaluationTool


class GoldenMaterialCase(_StrictModel):
    """确定性材料规则与引用用例。"""

    case_id: CaseId
    category: Literal[GoldenCaseCategory.MATERIAL_CHECK]
    title: str = Field(min_length=1, max_length=100)
    query: QueryText
    expected_application_type: ApplicationType
    expected_mode: MaterialCheckMode
    expected_required_materials: dict[str, int]
    expected_missing_materials: dict[str, int]
    expected_materials_complete: bool | None
    expected_clarification: bool
    expected_sensitive_materials: tuple[str, ...] = ()
    expected_citations: tuple[ExpectedCitation, ...]


class GoldenApprovalCase(_StrictModel):
    """确定性审批路线与引用用例。"""

    case_id: CaseId
    category: Literal[GoldenCaseCategory.APPROVAL_ROUTE]
    title: str = Field(min_length=1, max_length=100)
    query: QueryText
    expected_application_type: ApprovalApplicationType
    expected_approval_level: ApprovalLevel
    expected_amount: Decimal | None
    expected_leave_days: Decimal | None
    expected_approvers: tuple[ApproverCode, ...]
    expected_special_conditions: tuple[str, ...] = ()
    expected_clarification: bool
    expected_citations: tuple[ExpectedCitation, ...]


GoldenCase = Annotated[
    GoldenRoutingCase | GoldenMaterialCase | GoldenApprovalCase,
    Field(discriminator="category"),
]
GOLDEN_CASE_ADAPTER = TypeAdapter(GoldenCase)


class EvaluationThresholds(_StrictModel):
    """五项指标的默认简历版 v1.0 质量门禁。"""

    intent_accuracy: float = Field(default=0.90, ge=0.0, le=1.0)
    tool_selection_accuracy: float = Field(default=1.0, ge=0.0, le=1.0)
    material_check_accuracy: float = Field(default=1.0, ge=0.0, le=1.0)
    approval_route_accuracy: float = Field(default=1.0, ge=0.0, le=1.0)
    citation_accuracy: float = Field(default=1.0, ge=0.0, le=1.0)

    def for_metric(self, metric: EvaluationMetric) -> float:
        return float(getattr(self, metric.value))


class EvaluationAssertion(_StrictModel):
    """一个可定位到期望值和实际值的原子断言。"""

    name: str = Field(min_length=1, max_length=100)
    passed: bool
    expected: JsonValue
    actual: JsonValue


class CaseDimensionResult(_StrictModel):
    """单条用例在某个指标维度上的结果。"""

    metric: EvaluationMetric
    passed: bool
    assertions: tuple[EvaluationAssertion, ...]


class EvaluationCaseResult(_StrictModel):
    """单条黄金用例的完整执行结果。"""

    case_id: str
    category: GoldenCaseCategory
    title: str
    query: str
    passed: bool
    duration_ms: float = Field(ge=0.0)
    dimensions: tuple[CaseDimensionResult, ...]


class MetricSummary(_StrictModel):
    """一项指标的通过数、准确率和门禁状态。"""

    metric: EvaluationMetric
    passed_cases: int = Field(ge=0)
    total_cases: int = Field(ge=0)
    accuracy: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    meets_threshold: bool


class EvaluationReport(_StrictModel):
    """可写入 JSON 和 Markdown 的结构化评测报告。"""

    schema_version: Literal["1.0"] = "1.0"
    suite_name: Literal["enterprise_policy_agent_golden_set"] = (
        "enterprise_policy_agent_golden_set"
    )
    evaluation_mode: EvaluationMode
    intent_provider: str = Field(min_length=1, max_length=100)
    live_intent_llm_calls: bool
    generated_at: datetime
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_ms: float = Field(ge=0.0)
    total_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    quality_gate_passed: bool
    metrics: tuple[MetricSummary, ...]
    failed_case_ids: tuple[str, ...]
    case_results: tuple[EvaluationCaseResult, ...]
