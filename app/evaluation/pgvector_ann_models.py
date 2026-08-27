from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.retrieval_models import (
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.performance.models import PerformanceEnvironment


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HnswConfiguration(_StrictModel):
    m: int = Field(ge=2, le=100)
    ef_construction: int = Field(ge=4, le=1_000)
    ef_search: int = Field(ge=1, le=1_000)

    @model_validator(mode="after")
    def validate_construction_breadth(self) -> HnswConfiguration:
        if self.ef_construction < 2 * self.m:
            raise ValueError("ef_construction must be at least twice m")
        return self

    @property
    def identity(self) -> str:
        return f"m{self.m}-efc{self.ef_construction}-efs{self.ef_search}"


class PgvectorAnnPoint(_StrictModel):
    backend: Literal["exact", "hnsw"]
    configuration: HnswConfiguration | None
    query_samples: int = Field(ge=1)
    index_build_ms: float | None = Field(default=None, ge=0.0)
    ann_recall_at_5: float = Field(ge=0.0, le=1.0)
    judged_recall_at_5: float = Field(ge=0.0, le=1.0)
    mrr_at_5: float = Field(ge=0.0, le=1.0)
    ndcg_at_5: float = Field(ge=0.0, le=1.0)
    minimum_ms: float = Field(ge=0.0)
    average_ms: float = Field(ge=0.0)
    p50_ms: float = Field(ge=0.0)
    p95_ms: float = Field(ge=0.0)
    maximum_ms: float = Field(ge=0.0)
    error_count: int = Field(ge=0)
    errors: tuple[str, ...] = ()
    meets_quality_gate: bool
    pareto_optimal: bool


class PgvectorAnnExperimentReport(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    suite_name: Literal["enterprise_policy_agent_pgvector_hnsw"] = (
        "enterprise_policy_agent_pgvector_hnsw"
    )
    evaluation_mode: RetrievalEvaluationMode
    embedding_provider: str = Field(min_length=1, max_length=200)
    source_collection: str = Field(min_length=1, max_length=200)
    requested_device: str | None
    embedding_batch_size: int = Field(ge=1)
    external_model_calls: bool
    model_download_may_be_required: bool
    generated_at: datetime
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_cases: int = Field(ge=1)
    total_judgments: int = Field(ge=1)
    final_top_k: Literal[5] = 5
    warmup_iterations: int = Field(ge=0)
    measured_repetitions: int = Field(ge=1)
    minimum_ann_recall_at_5: float = Field(ge=0.0, le=1.0)
    thresholds: RetrievalEvaluationThresholds
    default_configuration: HnswConfiguration
    environment: PerformanceEnvironment
    security_boundary: Literal["materialize_authorized_scope_before_hnsw"] = (
        "materialize_authorized_scope_before_hnsw"
    )
    experiment_completed: bool
    exact_baseline_passed: bool
    default_configuration_passed: bool
    quality_gate_passed: bool
    pareto_configurations: tuple[str, ...]
    points: tuple[PgvectorAnnPoint, ...]
