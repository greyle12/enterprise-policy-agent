from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from math import log2
from time import perf_counter
from typing import Protocol

from app.evaluation.retrieval_models import (
    RetrievalCase,
    RetrievalCaseChannelResult,
    RetrievalCaseResult,
    RetrievalChannelSummary,
    RetrievalEvaluationMode,
    RetrievalEvaluationReport,
    RetrievalEvaluationThresholds,
)
from app.rag.policy_retriever import PolicyRetrievalResult, RetrievalMethod

DEFAULT_RETRIEVAL_CHANNELS = (
    RetrievalMethod.VECTOR,
    RetrievalMethod.BM25,
    RetrievalMethod.HYBRID,
    RetrievalMethod.RERANKED,
)
DEFAULT_RETRIEVAL_KS = (1, 3, 5)
DEFAULT_RETRIEVAL_CANDIDATE_K = 20


class RetrievalEvaluationTarget(Protocol):
    """Authorization-bound retrieval surface consumed by the evaluator."""

    def search(self, query: str, *, top_k: int = 5) -> list[PolicyRetrievalResult]: ...

    def search_keywords(self, query: str, *, top_k: int = 5) -> list[PolicyRetrievalResult]: ...

    def search_hybrid(
        self, query: str, *, top_k: int = 5, candidate_k: int | None = None
    ) -> list[PolicyRetrievalResult]: ...

    def search_reranked(
        self, query: str, *, top_k: int = 5, candidate_k: int | None = None
    ) -> list[PolicyRetrievalResult]: ...


def recall_at_k(retrieved_ids: Sequence[str], relevant_ids: Sequence[str], *, k: int) -> float:
    """Return the fraction of judged-relevant chunks present in the top K."""

    if k < 1:
        raise ValueError("k must be greater than zero")
    relevant = frozenset(relevant_ids)
    if not relevant:
        raise ValueError("relevant_ids must not be empty")
    retrieved = frozenset(retrieved_ids[:k])
    return len(retrieved.intersection(relevant)) / len(relevant)


def reciprocal_rank(
    retrieved_ids: Sequence[str], relevant_ids: Sequence[str], *, k: int
) -> tuple[int | None, float]:
    """Return first relevant rank and reciprocal rank, truncated at K."""

    if k < 1:
        raise ValueError("k must be greater than zero")
    relevant = frozenset(relevant_ids)
    if not relevant:
        raise ValueError("relevant_ids must not be empty")
    for rank, chunk_id in enumerate(retrieved_ids[:k], start=1):
        if chunk_id in relevant:
            return rank, 1.0 / rank
    return None, 0.0


def ndcg_at_k(
    retrieved_ids: Sequence[str],
    relevance_by_id: dict[str, int],
    *,
    k: int,
) -> float:
    """Return normalized discounted cumulative gain using exponential gains."""

    if k < 1:
        raise ValueError("k must be greater than zero")
    if not relevance_by_id:
        raise ValueError("relevance_by_id must not be empty")
    if any(
        isinstance(grade, bool) or not isinstance(grade, int) or grade not in {1, 2, 3}
        for grade in relevance_by_id.values()
    ):
        raise ValueError("relevance grades must be integers from one to three")

    seen: set[str] = set()
    dcg = 0.0
    for rank, chunk_id in enumerate(retrieved_ids[:k], start=1):
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        grade = relevance_by_id.get(chunk_id, 0)
        dcg += (2**grade - 1) / log2(rank + 1)

    ideal_grades = sorted(relevance_by_id.values(), reverse=True)[:k]
    ideal_dcg = sum(
        (2**grade - 1) / log2(rank + 1) for rank, grade in enumerate(ideal_grades, start=1)
    )
    return dcg / ideal_dcg


class RetrievalEvaluationRunner:
    """Measure Vector/BM25/Hybrid/Reranked output without invoking answer generation."""

    def __init__(
        self,
        *,
        retriever: RetrievalEvaluationTarget,
        evaluation_mode: RetrievalEvaluationMode,
        embedding_provider: str,
        reranker_provider: str,
        external_model_calls: bool,
        dataset_sha256: str,
        corpus_sha256: str,
        channels: Sequence[RetrievalMethod] = DEFAULT_RETRIEVAL_CHANNELS,
        ks: Sequence[int] = DEFAULT_RETRIEVAL_KS,
        candidate_k: int = DEFAULT_RETRIEVAL_CANDIDATE_K,
        thresholds: RetrievalEvaluationThresholds | None = None,
        clock: Callable[[], datetime] | None = None,
        timer: Callable[[], float] | None = None,
    ) -> None:
        channel_tuple = tuple(channels)
        ks_tuple = tuple(sorted(set(ks)))
        resolved_thresholds = thresholds or RetrievalEvaluationThresholds()
        if not channel_tuple or len(set(channel_tuple)) != len(channel_tuple):
            raise ValueError("channels must be non-empty and unique")
        if not ks_tuple or any(k < 1 for k in ks_tuple):
            raise ValueError("ks must contain positive integers")
        if candidate_k < max(ks_tuple):
            raise ValueError("candidate_k must be greater than or equal to max(ks)")
        if resolved_thresholds.gate_k not in ks_tuple:
            raise ValueError("threshold gate_k must be present in ks")
        missing_channels = set(resolved_thresholds.required_channels).difference(channel_tuple)
        if missing_channels:
            raise ValueError("quality-gate channels must be included in channels")

        self._retriever = retriever
        self._evaluation_mode = evaluation_mode
        self._embedding_provider = embedding_provider
        self._reranker_provider = reranker_provider
        self._external_model_calls = external_model_calls
        self._dataset_sha256 = dataset_sha256
        self._corpus_sha256 = corpus_sha256
        self._channels = channel_tuple
        self._ks = ks_tuple
        self._candidate_k = candidate_k
        self._thresholds = resolved_thresholds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timer = timer or perf_counter

    def _retrieve(self, channel: RetrievalMethod, query: str) -> list[PolicyRetrievalResult]:
        top_k = max(self._ks)
        if channel is RetrievalMethod.VECTOR:
            return self._retriever.search(query, top_k=top_k)
        if channel is RetrievalMethod.BM25:
            return self._retriever.search_keywords(query, top_k=top_k)
        if channel is RetrievalMethod.HYBRID:
            return self._retriever.search_hybrid(query, top_k=top_k, candidate_k=self._candidate_k)
        if channel is RetrievalMethod.RERANKED:
            return self._retriever.search_reranked(
                query, top_k=top_k, candidate_k=self._candidate_k
            )
        raise ValueError(f"unsupported retrieval channel: {channel}")

    def _run_channel(
        self, case: RetrievalCase, channel: RetrievalMethod
    ) -> RetrievalCaseChannelResult:
        started = self._timer()
        error: str | None = None
        try:
            results = self._retrieve(channel, case.query)
            retrieved_ids = tuple(result.chunk.chunk_id for result in results)
        except Exception as exc:  # noqa: BLE001 - one failed channel must remain reportable
            retrieved_ids = ()
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = max(0.0, (self._timer() - started) * 1000.0)
        first_rank, rr = reciprocal_rank(
            retrieved_ids,
            case.relevant_chunk_ids,
            k=self._thresholds.gate_k,
        )
        return RetrievalCaseChannelResult(
            channel=channel,
            retrieved_chunk_ids=retrieved_ids,
            recall_at_k={
                k: recall_at_k(retrieved_ids, case.relevant_chunk_ids, k=k) for k in self._ks
            },
            ndcg_at_k={
                k: ndcg_at_k(retrieved_ids, case.relevance_by_chunk_id, k=k) for k in self._ks
            },
            first_relevant_rank=first_rank,
            reciprocal_rank=rr,
            duration_ms=duration_ms,
            error=error,
        )

    def run(self, cases: Sequence[RetrievalCase]) -> RetrievalEvaluationReport:
        case_tuple = tuple(cases)
        if not case_tuple:
            raise ValueError("cases must not be empty")

        case_results = tuple(
            RetrievalCaseResult(
                case_id=case.case_id,
                title=case.title,
                query=case.query,
                relevant_chunk_ids=case.relevant_chunk_ids,
                judgments=case.judgments,
                channels=tuple(self._run_channel(case, channel) for channel in self._channels),
            )
            for case in case_tuple
        )

        summaries: list[RetrievalChannelSummary] = []
        for channel in self._channels:
            measurements = tuple(
                result
                for case_result in case_results
                for result in case_result.channels
                if result.channel is channel
            )
            recalls = {
                k: sum(item.recall_at_k[k] for item in measurements) / len(measurements)
                for k in self._ks
            }
            mrr = sum(item.reciprocal_rank for item in measurements) / len(measurements)
            ndcgs = {
                k: sum(item.ndcg_at_k[k] for item in measurements) / len(measurements)
                for k in self._ks
            }
            meets_gate: bool | None = None
            if channel in self._thresholds.required_channels:
                meets_gate = (
                    recalls[self._thresholds.gate_k] >= self._thresholds.minimum_recall
                    and mrr >= self._thresholds.minimum_mrr
                    and ndcgs[self._thresholds.gate_k] >= self._thresholds.minimum_ndcg
                    and not any(item.error for item in measurements)
                )
            summaries.append(
                RetrievalChannelSummary(
                    channel=channel,
                    case_count=len(measurements),
                    recall_at_k=recalls,
                    mrr_at_k=mrr,
                    ndcg_at_k=ndcgs,
                    average_duration_ms=(
                        sum(item.duration_ms for item in measurements) / len(measurements)
                    ),
                    error_count=sum(item.error is not None for item in measurements),
                    meets_quality_gate=meets_gate,
                )
            )

        required_summaries = [
            item for item in summaries if item.channel in self._thresholds.required_channels
        ]
        return RetrievalEvaluationReport(
            evaluation_mode=self._evaluation_mode,
            embedding_provider=self._embedding_provider,
            reranker_provider=self._reranker_provider,
            external_model_calls=self._external_model_calls,
            generated_at=self._clock(),
            dataset_sha256=self._dataset_sha256,
            corpus_sha256=self._corpus_sha256,
            total_cases=len(case_tuple),
            channels=self._channels,
            ks=self._ks,
            candidate_k=self._candidate_k,
            thresholds=self._thresholds,
            quality_gate_passed=all(item.meets_quality_gate is True for item in required_summaries),
            summaries=tuple(summaries),
            case_results=case_results,
        )
