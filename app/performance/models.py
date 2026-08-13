from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PerformanceScenarioName(StrEnum):
    """Day 22 离线基准覆盖的代表性系统路径。"""

    RUNTIME_STARTUP = "runtime_startup"
    POLICY_RAG_ANSWER = "policy_rag_answer"
    AGENT_MATERIAL_ROUTE = "agent_material_route"
    AGENT_APPROVAL_ROUTE = "agent_approval_route"
    POLICY_RESEARCH_HYBRID = "policy_research_hybrid"


class PerformanceBudget(_StrictModel):
    """一个场景允许的 p95 延迟和错误率上限。"""

    scenario: PerformanceScenarioName
    max_p95_ms: float = Field(gt=0.0)
    max_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class PerformanceSample(_StrictModel):
    """单次测量结果；失败只记录异常类型，不记录异常正文。"""

    iteration: int = Field(ge=1)
    duration_ms: float = Field(ge=0.0)
    succeeded: bool
    error_type: str | None = Field(default=None, min_length=1, max_length=100)


class PerformanceScenarioResult(_StrictModel):
    """一个场景的样本、分位数和预算判定。"""

    scenario: PerformanceScenarioName
    description: str = Field(min_length=1, max_length=300)
    sample_count: int = Field(ge=1)
    error_count: int = Field(ge=0)
    error_rate: float = Field(ge=0.0, le=1.0)
    minimum_ms: float = Field(ge=0.0)
    average_ms: float = Field(ge=0.0)
    p50_ms: float = Field(ge=0.0)
    p95_ms: float = Field(ge=0.0)
    maximum_ms: float = Field(ge=0.0)
    budget: PerformanceBudget
    budget_utilization: float = Field(ge=0.0)
    meets_budget: bool
    samples: tuple[PerformanceSample, ...]


class BottleneckCandidate(_StrictModel):
    """按观测 p95 排序的候选瓶颈，而不是未经验证的优化结论。"""

    rank: int = Field(ge=1)
    scenario: PerformanceScenarioName
    p95_ms: float = Field(ge=0.0)
    share_of_slowest: float = Field(ge=0.0, le=1.0)
    budget_utilization: float = Field(ge=0.0)


class PerformanceEnvironment(_StrictModel):
    """不包含主机名、用户名或绝对路径的可比较运行环境摘要。"""

    python_version: str = Field(min_length=1, max_length=50)
    operating_system: str = Field(min_length=1, max_length=50)
    machine: str = Field(min_length=1, max_length=50)


class PerformanceReport(_StrictModel):
    """可写入 JSON / Markdown 的 Day 22 离线性能报告。"""

    schema_version: Literal["1.0"] = "1.0"
    suite_name: Literal["enterprise_policy_agent_offline_performance"] = (
        "enterprise_policy_agent_offline_performance"
    )
    generated_at: datetime
    benchmark_mode: Literal["offline"] = "offline"
    budget_source: Literal["day22_default_v1"] = "day22_default_v1"
    warmup_iterations: int = Field(ge=0)
    measured_iterations: int = Field(ge=1)
    duration_ms: float = Field(ge=0.0)
    network_calls: bool
    live_llm_calls: bool
    embedding_provider: str = Field(min_length=1, max_length=100)
    environment: PerformanceEnvironment
    quality_gate_passed: bool
    bottleneck_candidates: tuple[BottleneckCandidate, ...]
    scenario_results: tuple[PerformanceScenarioResult, ...]


class ProfileHotspot(_StrictModel):
    """cProfile 中一个项目函数的累计耗时热点。"""

    rank: int = Field(ge=1)
    path: str = Field(min_length=1, max_length=500)
    line_number: int = Field(ge=1)
    function_name: str = Field(min_length=1, max_length=300)
    primitive_calls: int = Field(ge=0)
    total_calls: int = Field(ge=0)
    own_time_ms: float = Field(ge=0.0)
    cumulative_time_ms: float = Field(ge=0.0)


class CProfileReport(_StrictModel):
    """仅保留 app 相对路径的确定性 cProfile 热点摘要。"""

    schema_version: Literal["1.0"] = "1.0"
    profiler: Literal["cProfile"] = "cProfile"
    generated_at: datetime
    total_profiled_ms: float = Field(ge=0.0)
    total_function_entries: int = Field(ge=0)
    project_function_entries: int = Field(ge=0)
    sort_key: Literal["cumulative_time"] = "cumulative_time"
    hotspots: tuple[ProfileHotspot, ...]
