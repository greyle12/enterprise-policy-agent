from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PromptSecurityMetricsResponse(BaseModel):
    user_inputs_checked: int = Field(ge=0)
    user_inputs_blocked: int = Field(ge=0)
    evidence_chunks_checked: int = Field(ge=0)
    evidence_chunks_quarantined: int = Field(ge=0)
    llm_calls_avoided: int = Field(ge=0)


class PromptSecurityStatusResponse(BaseModel):
    """Content-free, process-local security counters."""

    schema_version: Literal["1.0"] = "1.0"
    state: Literal["enabled"] = "enabled"
    rule_set_version: str = Field(min_length=1, max_length=32)
    raw_content_recorded: Literal[False] = False
    metrics: PromptSecurityMetricsResponse
