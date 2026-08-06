from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.rag.policy_context import PolicyCitation


class ApplicationType(StrEnum):
    """材料检查当前支持的业务申请类型。"""

    PURCHASE = "purchase"
    TRAVEL_REIMBURSEMENT = "travel_reimbursement"
    LEAVE = "leave"
    EXPENSE_REIMBURSEMENT = "expense_reimbursement"


class MaterialCheckMode(StrEnum):
    """用户是查询材料要求，还是比对已有材料。"""

    REQUIREMENTS = "requirements"
    COMPARISON = "comparison"


@dataclass(frozen=True, slots=True)
class MaterialRequirement:
    """一项由制度规则产生的必需材料。"""

    material_type: str
    display_name: str
    reason: str
    required_count: int = 1
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class ProvidedMaterial:
    """从用户文本中识别出的已提供材料及数量。"""

    material_type: str
    display_name: str
    provided_count: int


@dataclass(frozen=True, slots=True)
class MissingMaterial:
    """材料比对后仍缺少的数量。"""

    material_type: str
    display_name: str
    missing_count: int
    reason: str
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class MaterialCheckResult:
    """一次材料要求查询或已有材料比对结果。"""

    application_type: ApplicationType | None
    mode: MaterialCheckMode
    required_materials: tuple[MaterialRequirement, ...]
    provided_materials: tuple[ProvidedMaterial, ...]
    missing_materials: tuple[MissingMaterial, ...]
    materials_complete: bool | None
    clarification_question: str | None
    notes: tuple[str, ...]
    citations: tuple[PolicyCitation, ...]


@dataclass(frozen=True, slots=True)
class MaterialCheckAnswer:
    """供 AgentRouter 使用的材料检查回答。"""

    request: str
    result: MaterialCheckResult
    reply: str

