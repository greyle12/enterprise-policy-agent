from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.retrieval_models import (
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.performance.models import PerformanceEnvironment
from app.rag.policy_retriever import RetrievalMethod


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateWindowPoint(_StrictModel):
    """Quality and latency observed for one channel and candidate window."""

    channel: RetrievalMethod
    candidate_k: int = Field(ge=5, le=1_000)
    query_samples: int = Field(ge=1)
    recall_at_5: float = Field(ge=0.0, le=1.0)
    mrr_at_5: float = Field(ge=0.0, le=1.0)
    ndcg_at_5: float = Field(ge=0.0, le=1.0)
    minimum_ms: float = Field(ge=0.0)
    average_ms: float = Field(ge=0.0)
    p50_ms: float = Field(ge=0.0)
    p95_ms: float = Field(ge=0.0)
    maximum_ms: float = Field(ge=0.0)
    error_count: int = Field(ge=0)
    meets_quality_gate: bool
    pareto_optimal: bool


class CandidateWindowExperimentReport(_StrictModel):
    """A controlled candidate-window quality/latency experiment."""

    schema_version: Literal["1.0"] = "1.0"
    suite_name: Literal["enterprise_policy_agent_candidate_window_sweep"] = (
        "enterprise_policy_agent_candidate_window_sweep"
    )
    evaluation_mode: RetrievalEvaluationMode
    embedding_provider: str = Field(min_length=1, max_length=200)
    reranker_provider: str = Field(min_length=1, max_length=200)
    requested_device: str | None
    embedding_batch_size: int = Field(ge=1)
    reranker_batch_size: int = Field(ge=1)
    external_model_calls: bool
    model_download_may_be_required: bool
    generated_at: datetime
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_cases: int = Field(ge=1)
    total_judgments: int = Field(ge=1)
    final_top_k: Literal[5] = 5
    candidate_ks: tuple[int, ...] = Field(min_length=1)
    default_candidate_k: int = Field(ge=5)
    warmup_iterations: int = Field(ge=0)
    measured_repetitions: int = Field(ge=1)
    thresholds: RetrievalEvaluationThresholds
    environment: PerformanceEnvironment
    experiment_completed: bool
    default_quality_gate_passed: bool
    quality_gate_passed: bool
    pareto_frontier: dict[RetrievalMethod, tuple[int, ...]]
    points: tuple[CandidateWindowPoint, ...]
