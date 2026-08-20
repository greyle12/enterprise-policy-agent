from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class PortfolioDemoScenario(StrEnum):
    """Capabilities presented by the deterministic Day 30 demo."""

    RAG_CITATION = "rag_citation"
    MATERIAL_RULES = "material_rules"
    APPROVAL_ROUTE = "approval_route"
    HUMAN_IN_LOOP = "human_in_loop"
    RESEARCH_BOUNDARY = "research_boundary"
    SECURITY_BOUNDARY = "security_boundary"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PortfolioScenarioResult(_StrictModel):
    scenario: PortfolioDemoScenario
    title: str = Field(min_length=1, max_length=100)
    capability: str = Field(min_length=1, max_length=240)
    passed: bool
    duration_ms: float = Field(ge=0.0)
    observations: dict[str, JsonValue]
    error_type: str | None = Field(default=None, max_length=100)


class PortfolioDemoReport(_StrictModel):
    """Machine-readable evidence for the final offline portfolio walkthrough."""

    schema_version: Literal["1.0"] = "1.0"
    suite_name: Literal["enterprise_policy_agent_portfolio_demo"] = (
        "enterprise_policy_agent_portfolio_demo"
    )
    release_label: Literal["day30"] = "day30"
    execution_mode: Literal["offline"] = "offline"
    generated_at: datetime
    duration_ms: float = Field(ge=0.0)
    network_calls: Literal[False] = False
    live_llm_calls: Literal[False] = False
    policy_documents: int = Field(ge=1)
    total_scenarios: int = Field(ge=1)
    passed_scenarios: int = Field(ge=0)
    failed_scenarios: int = Field(ge=0)
    quality_gate_passed: bool
    scenarios: tuple[PortfolioScenarioResult, ...]
