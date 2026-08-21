from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.rag.policy_retriever import RetrievalMethod

RetrievalCaseId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^RET-[0-9]{3}$"),
]
ChunkId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalEvaluationMode(StrEnum):
    """Embedding and reranker providers used by a retrieval evaluation run."""

    OFFLINE = "offline"
    BGE = "bge"


class RetrievalCase(_StrictModel):
    """One query with an explicitly judged set of relevant policy chunks."""

    case_id: RetrievalCaseId
    title: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=2, max_length=500)
    relevant_chunk_ids: tuple[ChunkId, ...] = Field(min_length=1)
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_unique_relevant_chunks(self) -> RetrievalCase:
        if len(set(self.relevant_chunk_ids)) != len(self.relevant_chunk_ids):
            raise ValueError("relevant_chunk_ids must be unique")
        return self


class RetrievalEvaluationThresholds(_StrictModel):
    """Quality gate applied to the production Hybrid and Reranked channels."""

    gate_k: int = Field(default=5, ge=1, le=100)
    minimum_recall: float = Field(default=0.80, ge=0.0, le=1.0)
    minimum_mrr: float = Field(default=0.80, ge=0.0, le=1.0)
    required_channels: tuple[RetrievalMethod, ...] = (
        RetrievalMethod.HYBRID,
        RetrievalMethod.RERANKED,
    )


class RetrievalCaseChannelResult(_StrictModel):
    """Ranked output and retrieval metrics for one case and one channel."""

    channel: RetrievalMethod
    retrieved_chunk_ids: tuple[str, ...]
    recall_at_k: dict[int, float]
    first_relevant_rank: int | None = Field(default=None, ge=1)
    reciprocal_rank: float = Field(ge=0.0, le=1.0)
    duration_ms: float = Field(ge=0.0)
    error: str | None = None


class RetrievalCaseResult(_StrictModel):
    """All retrieval-channel measurements for one judged query."""

    case_id: str
    title: str
    query: str
    relevant_chunk_ids: tuple[str, ...]
    channels: tuple[RetrievalCaseChannelResult, ...]


class RetrievalChannelSummary(_StrictModel):
    """Macro-averaged Recall@K and MRR@K for one retrieval channel."""

    channel: RetrievalMethod
    case_count: int = Field(ge=1)
    recall_at_k: dict[int, float]
    mrr_at_k: float = Field(ge=0.0, le=1.0)
    average_duration_ms: float = Field(ge=0.0)
    error_count: int = Field(ge=0)
    meets_quality_gate: bool | None


class RetrievalEvaluationReport(_StrictModel):
    """Auditable retrieval-only evaluation report, separate from answer accuracy."""

    schema_version: Literal["1.0"] = "1.0"
    suite_name: Literal["enterprise_policy_agent_retrieval"] = "enterprise_policy_agent_retrieval"
    evaluation_mode: RetrievalEvaluationMode
    embedding_provider: str = Field(min_length=1, max_length=200)
    reranker_provider: str = Field(min_length=1, max_length=200)
    external_model_calls: bool
    generated_at: datetime
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_cases: int = Field(ge=1)
    channels: tuple[RetrievalMethod, ...]
    ks: tuple[int, ...]
    candidate_k: int = Field(ge=1)
    thresholds: RetrievalEvaluationThresholds
    quality_gate_passed: bool
    summaries: tuple[RetrievalChannelSummary, ...]
    case_results: tuple[RetrievalCaseResult, ...]
