from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class IntentType(StrEnum):
    """企业制度 Agent 当前支持的用户意图。"""

    POLICY_QUERY = "policy_query"
    MATERIAL_CHECK = "material_check"
    APPROVAL_QUERY = "approval_query"
    DRAFT_GENERATION = "draft_generation"
    DRAFT_UPDATE = "draft_update"
    DRAFT_CONFIRMATION = "draft_confirmation"
    DRAFT_SUBMISSION = "draft_submission"
    DRAFT_CANCELLATION = "draft_cancellation"
    UNKNOWN = "unknown"


IntentReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=200,
    ),
]


class IntentClassification(BaseModel):
    """一次意图分类的结构化结果。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    intent: IntentType
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        strict=True,
    )
    reason: IntentReason
