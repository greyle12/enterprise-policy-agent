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


class ConcurrencyLoadScenarioName(StrEnum):
    """Day 25 离线并发负载覆盖的三种请求分布。"""

    HOT_KEY_BURST = "hot_key_burst"
    MIXED_HOTSET = "mixed_hotset"
    UNIQUE_KEY_FANOUT = "unique_key_fanout"


class BatchOptimizationScenarioName(StrEnum):
    """Day 26 离线批处理对比覆盖的模型工作负载。"""

    EMBEDDING_DOCUMENTS = "embedding_documents"
    RERANKER_CANDIDATES = "reranker_candidates"


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


class ConcurrencyLoadSample(_StrictModel):
    """一个并发客户端请求的端到端延迟和稳定错误类型。"""

    request_id: int = Field(ge=1)
    duration_ms: float = Field(ge=0.0)
    succeeded: bool
    error_type: str | None = Field(default=None, min_length=1, max_length=100)


class ConcurrencyLoadScenarioResult(_StrictModel):
    """一个并发请求分布的吞吐、延迟和上游放大结果。"""

    scenario: ConcurrencyLoadScenarioName
    description: str = Field(min_length=1, max_length=300)
    request_count: int = Field(ge=1)
    configured_concurrency: int = Field(ge=1)
    unique_request_keys: int = Field(ge=1)
    duration_ms: float = Field(gt=0.0)
    throughput_rps: float = Field(gt=0.0)
    error_count: int = Field(ge=0)
    error_rate: float = Field(ge=0.0, le=1.0)
    minimum_ms: float = Field(ge=0.0)
    average_ms: float = Field(ge=0.0)
    p50_ms: float = Field(ge=0.0)
    p95_ms: float = Field(ge=0.0)
    maximum_ms: float = Field(ge=0.0)
    client_peak_in_flight: int = Field(ge=0)
    provider_peak_in_flight: int = Field(ge=0)
    expected_upstream_calls: int = Field(ge=1)
    upstream_calls: int = Field(ge=0)
    upstream_call_ratio: float = Field(ge=0.0)
    upstream_call_amplification: float = Field(ge=0.0)
    cache_hits: int = Field(ge=0)
    coalesced_requests: int = Field(ge=0)
    meets_contract: bool
    samples: tuple[ConcurrencyLoadSample, ...]


class ConcurrencyLoadReport(_StrictModel):
    """可写入 JSON / Markdown 的 Day 25 离线并发负载报告。"""

    schema_version: Literal["1.0"] = "1.0"
    suite_name: Literal["enterprise_policy_agent_offline_concurrency"] = (
        "enterprise_policy_agent_offline_concurrency"
    )
    generated_at: datetime
    benchmark_mode: Literal["offline"] = "offline"
    request_count: int = Field(ge=1)
    configured_concurrency: int = Field(ge=1)
    simulated_provider_latency_ms: float = Field(gt=0.0)
    duration_ms: float = Field(gt=0.0)
    network_calls: bool
    live_llm_calls: bool
    environment: PerformanceEnvironment
    quality_gate_passed: bool
    decision: Literal["collect_live_provider_baseline_before_enabling_process_limit"] = (
        "collect_live_provider_baseline_before_enabling_process_limit"
    )
    scenario_results: tuple[ConcurrencyLoadScenarioResult, ...]


class BatchOptimizationScenarioResult(_StrictModel):
    """逐条与批量模型调用的等价性、调用次数和吞吐对比。"""

    scenario: BatchOptimizationScenarioName
    description: str = Field(min_length=1, max_length=300)
    item_count: int = Field(ge=1)
    configured_batch_size: int = Field(ge=1)
    sequential_provider_calls: int = Field(ge=1)
    batched_provider_calls: int = Field(ge=1)
    provider_call_reduction: float = Field(ge=0.0, le=1.0)
    sequential_internal_batches: int = Field(ge=1)
    batched_internal_batches: int = Field(ge=1)
    sequential_duration_ms: float = Field(gt=0.0)
    batched_duration_ms: float = Field(gt=0.0)
    sequential_throughput_items_per_second: float = Field(gt=0.0)
    batched_throughput_items_per_second: float = Field(gt=0.0)
    throughput_speedup: float = Field(gt=0.0)
    outputs_equivalent: bool
    order_preserved: bool
    batched_faster: bool
    meets_contract: bool


class BatchOptimizationReport(_StrictModel):
    """可写入 JSON / Markdown 的 Day 26 离线批处理报告。"""

    schema_version: Literal["1.0"] = "1.0"
    suite_name: Literal["enterprise_policy_agent_offline_batch_optimization"] = (
        "enterprise_policy_agent_offline_batch_optimization"
    )
    generated_at: datetime
    benchmark_mode: Literal["offline"] = "offline"
    item_count: int = Field(ge=1)
    configured_batch_size: int = Field(ge=1)
    simulated_call_overhead_ms: float = Field(gt=0.0)
    simulated_batch_latency_ms: float = Field(gt=0.0)
    duration_ms: float = Field(gt=0.0)
    network_calls: bool
    live_model_calls: bool
    environment: PerformanceEnvironment
    quality_gate_passed: bool
    decision: Literal["batch_embedding_and_reranker_keep_llm_requests_independent"] = (
        "batch_embedding_and_reranker_keep_llm_requests_independent"
    )
    scenario_results: tuple[BatchOptimizationScenarioResult, ...]


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
