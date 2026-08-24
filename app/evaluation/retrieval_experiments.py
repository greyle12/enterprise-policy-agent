from __future__ import annotations

import platform
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from statistics import fmean

from app.evaluation.retrieval_experiment_models import (
    CandidateWindowExperimentReport,
    CandidateWindowPoint,
)
from app.evaluation.retrieval_models import (
    RetrievalCase,
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.evaluation.retrieval_runner import (
    RetrievalEvaluationRunner,
    RetrievalEvaluationTarget,
)
from app.performance.benchmark import nearest_rank_percentile
from app.performance.models import PerformanceEnvironment
from app.rag.policy_retriever import RetrievalMethod

EXPERIMENT_CHANNELS = (RetrievalMethod.HYBRID, RetrievalMethod.RERANKED)
DEFAULT_CANDIDATE_WINDOWS = (5, 10, 20, 40)
DEFAULT_PRODUCTION_CANDIDATE_K = 20


def _environment() -> PerformanceEnvironment:
    return PerformanceEnvironment(
        python_version=platform.python_version(),
        operating_system=platform.system() or sys.platform,
        machine=platform.machine() or "unknown",
    )


def normalize_candidate_windows(
    candidate_ks: Sequence[int],
    *,
    default_candidate_k: int,
) -> tuple[int, ...]:
    """Validate and normalize a sweep before expensive model construction."""

    resolved = tuple(sorted(set(candidate_ks)))
    if not resolved:
        raise ValueError("candidate_ks must not be empty")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 1_000
        for value in resolved
    ):
        raise ValueError("candidate_ks must contain integers from five to one thousand")
    if default_candidate_k not in resolved:
        raise ValueError("default_candidate_k must be included in candidate_ks")
    return resolved


def _dominates(left: CandidateWindowPoint, right: CandidateWindowPoint) -> bool:
    quality_not_worse = (
        left.recall_at_5 >= right.recall_at_5
        and left.mrr_at_5 >= right.mrr_at_5
        and left.ndcg_at_5 >= right.ndcg_at_5
    )
    latency_not_worse = left.p95_ms <= right.p95_ms
    strictly_better = (
        left.recall_at_5 > right.recall_at_5
        or left.mrr_at_5 > right.mrr_at_5
        or left.ndcg_at_5 > right.ndcg_at_5
        or left.p95_ms < right.p95_ms
    )
    return quality_not_worse and latency_not_worse and strictly_better


def mark_pareto_frontier(
    points: Sequence[CandidateWindowPoint],
) -> tuple[CandidateWindowPoint, ...]:
    """Mark non-dominated candidate windows independently for each channel."""

    point_tuple = tuple(points)
    marked: list[CandidateWindowPoint] = []
    for point in point_tuple:
        peers = (candidate for candidate in point_tuple if candidate.channel is point.channel)
        dominated = any(_dominates(peer, point) for peer in peers if peer is not point)
        marked.append(point.model_copy(update={"pareto_optimal": not dominated}))
    return tuple(marked)


class CandidateWindowExperimentRunner:
    """Sweep RRF/reranker candidate windows on one already-built retriever."""

    def __init__(
        self,
        *,
        retriever: RetrievalEvaluationTarget,
        evaluation_mode: RetrievalEvaluationMode,
        embedding_provider: str,
        reranker_provider: str,
        requested_device: str | None,
        embedding_batch_size: int,
        reranker_batch_size: int,
        external_model_calls: bool,
        dataset_sha256: str,
        corpus_sha256: str,
        candidate_ks: Sequence[int] = DEFAULT_CANDIDATE_WINDOWS,
        default_candidate_k: int = DEFAULT_PRODUCTION_CANDIDATE_K,
        warmup_iterations: int = 1,
        measured_repetitions: int = 3,
        thresholds: RetrievalEvaluationThresholds | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        resolved_candidate_ks = normalize_candidate_windows(
            candidate_ks,
            default_candidate_k=default_candidate_k,
        )
        if warmup_iterations < 0:
            raise ValueError("warmup_iterations must not be negative")
        if measured_repetitions < 1:
            raise ValueError("measured_repetitions must be greater than zero")

        self._retriever = retriever
        self._evaluation_mode = evaluation_mode
        self._embedding_provider = embedding_provider
        self._reranker_provider = reranker_provider
        self._requested_device = requested_device
        self._embedding_batch_size = embedding_batch_size
        self._reranker_batch_size = reranker_batch_size
        self._external_model_calls = external_model_calls
        self._dataset_sha256 = dataset_sha256
        self._corpus_sha256 = corpus_sha256
        self._candidate_ks = resolved_candidate_ks
        self._default_candidate_k = default_candidate_k
        self._warmup_iterations = warmup_iterations
        self._measured_repetitions = measured_repetitions
        self._thresholds = thresholds or RetrievalEvaluationThresholds()
        if self._thresholds.gate_k != 5:
            raise ValueError("candidate-window experiments require gate_k=5")
        self._clock = clock or (lambda: datetime.now(UTC))

    def _evaluation_runner(self, candidate_k: int) -> RetrievalEvaluationRunner:
        return RetrievalEvaluationRunner(
            retriever=self._retriever,
            evaluation_mode=self._evaluation_mode,
            embedding_provider=self._embedding_provider,
            reranker_provider=self._reranker_provider,
            external_model_calls=self._external_model_calls,
            dataset_sha256=self._dataset_sha256,
            corpus_sha256=self._corpus_sha256,
            channels=EXPERIMENT_CHANNELS,
            candidate_k=candidate_k,
            thresholds=self._thresholds,
        )

    def run(self, cases: Sequence[RetrievalCase]) -> CandidateWindowExperimentReport:
        case_tuple = tuple(cases)
        if not case_tuple:
            raise ValueError("cases must not be empty")

        raw_points: list[CandidateWindowPoint] = []
        for candidate_k in self._candidate_ks:
            runner = self._evaluation_runner(candidate_k)
            for _ in range(self._warmup_iterations):
                runner.run(case_tuple)

            reports = tuple(runner.run(case_tuple) for _ in range(self._measured_repetitions))
            for channel in EXPERIMENT_CHANNELS:
                summaries = tuple(
                    summary
                    for report in reports
                    for summary in report.summaries
                    if summary.channel is channel
                )
                measurements = tuple(
                    measurement
                    for report in reports
                    for case in report.case_results
                    for measurement in case.channels
                    if measurement.channel is channel
                )
                durations = tuple(item.duration_ms for item in measurements)
                recall = fmean(item.recall_at_k[5] for item in summaries)
                mrr = fmean(item.mrr_at_k for item in summaries)
                ndcg = fmean(item.ndcg_at_k[5] for item in summaries)
                error_count = sum(item.error is not None for item in measurements)
                raw_points.append(
                    CandidateWindowPoint(
                        channel=channel,
                        candidate_k=candidate_k,
                        query_samples=len(measurements),
                        recall_at_5=recall,
                        mrr_at_5=mrr,
                        ndcg_at_5=ndcg,
                        minimum_ms=min(durations),
                        average_ms=fmean(durations),
                        p50_ms=nearest_rank_percentile(durations, 0.50),
                        p95_ms=nearest_rank_percentile(durations, 0.95),
                        maximum_ms=max(durations),
                        error_count=error_count,
                        meets_quality_gate=(
                            recall >= self._thresholds.minimum_recall
                            and mrr >= self._thresholds.minimum_mrr
                            and ndcg >= self._thresholds.minimum_ndcg
                            and error_count == 0
                        ),
                        pareto_optimal=False,
                    )
                )

        points = mark_pareto_frontier(raw_points)
        experiment_completed = all(point.error_count == 0 for point in points)
        default_points = tuple(
            point for point in points if point.candidate_k == self._default_candidate_k
        )
        default_quality_gate_passed = len(default_points) == len(EXPERIMENT_CHANNELS) and all(
            point.meets_quality_gate for point in default_points
        )
        pareto_frontier = {
            channel: tuple(
                point.candidate_k
                for point in points
                if point.channel is channel and point.pareto_optimal
            )
            for channel in EXPERIMENT_CHANNELS
        }
        return CandidateWindowExperimentReport(
            evaluation_mode=self._evaluation_mode,
            embedding_provider=self._embedding_provider,
            reranker_provider=self._reranker_provider,
            requested_device=self._requested_device,
            embedding_batch_size=self._embedding_batch_size,
            reranker_batch_size=self._reranker_batch_size,
            external_model_calls=self._external_model_calls,
            model_download_may_be_required=(self._evaluation_mode is RetrievalEvaluationMode.BGE),
            generated_at=self._clock(),
            dataset_sha256=self._dataset_sha256,
            corpus_sha256=self._corpus_sha256,
            total_cases=len(case_tuple),
            total_judgments=sum(len(case.judgments) for case in case_tuple),
            candidate_ks=self._candidate_ks,
            default_candidate_k=self._default_candidate_k,
            warmup_iterations=self._warmup_iterations,
            measured_repetitions=self._measured_repetitions,
            thresholds=self._thresholds,
            environment=_environment(),
            experiment_completed=experiment_completed,
            default_quality_gate_passed=default_quality_gate_passed,
            quality_gate_passed=(experiment_completed and default_quality_gate_passed),
            pareto_frontier=pareto_frontier,
            points=points,
        )
