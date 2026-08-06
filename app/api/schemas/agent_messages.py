from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.agent.intent import IntentType
from app.agent.router import AgentResponseStatus
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


class AgentMessageResponse(BaseModel):
    """统一 Agent 入口的路由结果。"""

    request: str
    classification: IntentClassificationResponse
    status: AgentResponseStatus
    reply: str
    citations: list[str]
    material_check: MaterialCheckResponse | None = None
