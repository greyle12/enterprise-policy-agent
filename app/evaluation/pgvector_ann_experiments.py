from __future__ import annotations

import platform
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import fmean
from time import perf_counter_ns
from typing import Protocol

from app.evaluation.pgvector_ann_models import (
    HnswConfiguration,
    PgvectorAnnExperimentReport,
    PgvectorAnnPoint,
)
from app.evaluation.retrieval_models import RetrievalCase, RetrievalEvaluationMode
from app.evaluation.retrieval_models import RetrievalEvaluationThresholds
from app.evaluation.retrieval_runner import ndcg_at_k, recall_at_k, reciprocal_rank
from app.performance.benchmark import nearest_rank_percentile
from app.performance.models import PerformanceEnvironment


class VectorRetrievalTarget(Protocol):
    def search(self, query: str, *, top_k: int = 5):
        """Return ranked objects exposing chunk.chunk_id."""


@dataclass(frozen=True, slots=True)
class PreparedHnswTarget:
    configuration: HnswConfiguration
    target: VectorRetrievalTarget
    index_build_ms: float


@dataclass(frozen=True, slots=True)
class _Measurement:
    case: RetrievalCase
    ranked_ids: tuple[str, ...]
    duration_ms: float
    error: str | None


def _environment() -> PerformanceEnvironment:
    return PerformanceEnvironment(
        python_version=platform.python_version(),
        operating_system=platform.system() or sys.platform,
        machine=platform.machine() or "unknown",
    )


def _measure(
    target: VectorRetrievalTarget,
    cases: Sequence[RetrievalCase],
) -> tuple[_Measurement, ...]:
    measurements: list[_Measurement] = []
    for case in cases:
        started = perf_counter_ns()
        try:
            results = target.search(case.query, top_k=5)
            ranked_ids = tuple(result.chunk.chunk_id for result in results)
            error = None
        except Exception as exc:  # noqa: BLE001 - experiment must record per-query failure
            ranked_ids = ()
            error = f"{type(exc).__name__}: {exc}"
        measurements.append(
            _Measurement(
                case=case,
                ranked_ids=ranked_ids,
                duration_ms=(perf_counter_ns() - started) / 1_000_000,
                error=error,
            )
        )
    return tuple(measurements)


def _dominates(left: PgvectorAnnPoint, right: PgvectorAnnPoint) -> bool:
    quality_not_worse = (
        left.ann_recall_at_5 >= right.ann_recall_at_5
        and left.judged_recall_at_5 >= right.judged_recall_at_5
        and left.mrr_at_5 >= right.mrr_at_5
        and left.ndcg_at_5 >= right.ndcg_at_5
    )
    latency_not_worse = left.p95_ms <= right.p95_ms
    strict = (
        left.ann_recall_at_5 > right.ann_recall_at_5
        or left.judged_recall_at_5 > right.judged_recall_at_5
        or left.mrr_at_5 > right.mrr_at_5
        or left.ndcg_at_5 > right.ndcg_at_5
        or left.p95_ms < right.p95_ms
    )
    return quality_not_worse and latency_not_worse and strict


def mark_ann_pareto(points: Sequence[PgvectorAnnPoint]) -> tuple[PgvectorAnnPoint, ...]:
    hnsw_points = tuple(point for point in points if point.backend == "hnsw")
    return tuple(
        point.model_copy(
            update={
                "pareto_optimal": (
                    point.backend == "hnsw"
                    and not any(
                        _dominates(peer, point) for peer in hnsw_points if peer is not point
                    )
                )
            }
        )
        for point in points
    )


class PgvectorAnnExperimentRunner:
    """Compare exact pgvector search with isolated authorized HNSW indexes."""

    def __init__(
        self,
        *,
        exact_target: VectorRetrievalTarget,
        hnsw_targets: Sequence[PreparedHnswTarget],
        default_configuration: HnswConfiguration,
        evaluation_mode: RetrievalEvaluationMode,
        embedding_provider: str,
        source_collection: str,
        requested_device: str | None,
        embedding_batch_size: int,
        external_model_calls: bool,
        dataset_sha256: str,
        corpus_sha256: str,
        warmup_iterations: int = 1,
        measured_repetitions: int = 3,
        minimum_ann_recall_at_5: float = 0.95,
        thresholds: RetrievalEvaluationThresholds | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        targets = tuple(hnsw_targets)
        identities = tuple(item.configuration.identity for item in targets)
        if not targets:
            raise ValueError("hnsw_targets must not be empty")
        if len(set(identities)) != len(identities):
            raise ValueError("HNSW configurations must be unique")
        if default_configuration.identity not in identities:
            raise ValueError("default_configuration must be included in hnsw_targets")
        if not source_collection.strip():
            raise ValueError("source_collection must not be blank")
        if warmup_iterations < 0 or measured_repetitions < 1:
            raise ValueError("warmups must be non-negative and repetitions must be positive")
        if not 0.0 <= minimum_ann_recall_at_5 <= 1.0:
            raise ValueError("minimum_ann_recall_at_5 must be between zero and one")
        resolved_thresholds = thresholds or RetrievalEvaluationThresholds()
        if resolved_thresholds.gate_k != 5:
            raise ValueError("pgvector ANN experiments require gate_k=5")

        self._exact_target = exact_target
        self._hnsw_targets = targets
        self._default_configuration = default_configuration
        self._evaluation_mode = evaluation_mode
        self._embedding_provider = embedding_provider
        self._source_collection = source_collection.strip()
        self._requested_device = requested_device
        self._embedding_batch_size = embedding_batch_size
        self._external_model_calls = external_model_calls
        self._dataset_sha256 = dataset_sha256
        self._corpus_sha256 = corpus_sha256
        self._warmups = warmup_iterations
        self._repetitions = measured_repetitions
        self._minimum_ann_recall = minimum_ann_recall_at_5
        self._thresholds = resolved_thresholds
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, cases: Sequence[RetrievalCase]) -> PgvectorAnnExperimentReport:
        case_tuple = tuple(cases)
        if not case_tuple:
            raise ValueError("cases must not be empty")

        targets = (("exact", None, self._exact_target, None),) + tuple(
            ("hnsw", item.configuration, item.target, item.index_build_ms)
            for item in self._hnsw_targets
        )
        for _ in range(self._warmups):
            for _, _, target, _ in targets:
                _measure(target, case_tuple)

        measured_by_backend: list[
            tuple[str, HnswConfiguration | None, float | None, tuple[_Measurement, ...]]
        ] = []
        for backend, configuration, target, build_ms in targets:
            measurements = tuple(
                item for _ in range(self._repetitions) for item in _measure(target, case_tuple)
            )
            measured_by_backend.append((backend, configuration, build_ms, measurements))

        exact_measurements = measured_by_backend[0][3]
        exact_rankings = {
            measurement.case.case_id: measurement.ranked_ids
            for measurement in exact_measurements[: len(case_tuple)]
        }
        raw_points = tuple(
            self._point(
                backend=backend,
                configuration=configuration,
                index_build_ms=build_ms,
                measurements=measurements,
                exact_rankings=exact_rankings,
            )
            for backend, configuration, build_ms, measurements in measured_by_backend
        )
        points = mark_ann_pareto(raw_points)
        exact_point = points[0]
        default_point = next(
            point
            for point in points
            if point.configuration is not None
            and point.configuration.identity == self._default_configuration.identity
        )
        completed = all(point.error_count == 0 for point in points)
        return PgvectorAnnExperimentReport(
            evaluation_mode=self._evaluation_mode,
            embedding_provider=self._embedding_provider,
            source_collection=self._source_collection,
            requested_device=self._requested_device,
            embedding_batch_size=self._embedding_batch_size,
            external_model_calls=self._external_model_calls,
            model_download_may_be_required=(self._evaluation_mode is RetrievalEvaluationMode.BGE),
            generated_at=self._clock(),
            dataset_sha256=self._dataset_sha256,
            corpus_sha256=self._corpus_sha256,
            total_cases=len(case_tuple),
            total_judgments=sum(len(case.judgments) for case in case_tuple),
            warmup_iterations=self._warmups,
            measured_repetitions=self._repetitions,
            minimum_ann_recall_at_5=self._minimum_ann_recall,
            thresholds=self._thresholds,
            default_configuration=self._default_configuration,
            environment=_environment(),
            experiment_completed=completed,
            exact_baseline_passed=exact_point.meets_quality_gate,
            default_configuration_passed=default_point.meets_quality_gate,
            quality_gate_passed=(
                completed and exact_point.meets_quality_gate and default_point.meets_quality_gate
            ),
            pareto_configurations=tuple(
                point.configuration.identity
                for point in points
                if point.configuration is not None and point.pareto_optimal
            ),
            points=points,
        )

    def _point(
        self,
        *,
        backend: str,
        configuration: HnswConfiguration | None,
        index_build_ms: float | None,
        measurements: Sequence[_Measurement],
        exact_rankings: dict[str, tuple[str, ...]],
    ) -> PgvectorAnnPoint:
        durations = tuple(item.duration_ms for item in measurements)
        errors = tuple(sorted({item.error for item in measurements if item.error is not None}))
        error_count = sum(item.error is not None for item in measurements)
        judged_recall = fmean(
            recall_at_k(item.ranked_ids, item.case.relevant_chunk_ids, k=5) for item in measurements
        )
        mrr = fmean(
            reciprocal_rank(item.ranked_ids, item.case.relevant_chunk_ids, k=5)[1]
            for item in measurements
        )
        ndcg = fmean(
            ndcg_at_k(item.ranked_ids, item.case.relevance_by_chunk_id, k=5)
            for item in measurements
        )
        ann_recall = (
            (1.0 if error_count == 0 else 0.0)
            if backend == "exact"
            else fmean(
                len(set(item.ranked_ids[:5]).intersection(exact_rankings[item.case.case_id]))
                / max(1, len(exact_rankings[item.case.case_id][:5]))
                for item in measurements
            )
        )
        passed = (
            ann_recall >= self._minimum_ann_recall
            and judged_recall >= self._thresholds.minimum_recall
            and mrr >= self._thresholds.minimum_mrr
            and ndcg >= self._thresholds.minimum_ndcg
            and error_count == 0
        )
        return PgvectorAnnPoint(
            backend=backend,
            configuration=configuration,
            query_samples=len(measurements),
            index_build_ms=index_build_ms,
            ann_recall_at_5=ann_recall,
            judged_recall_at_5=judged_recall,
            mrr_at_5=mrr,
            ndcg_at_5=ndcg,
            minimum_ms=min(durations),
            average_ms=fmean(durations),
            p50_ms=nearest_rank_percentile(durations, 0.50),
            p95_ms=nearest_rank_percentile(durations, 0.95),
            maximum_ms=max(durations),
            error_count=error_count,
            errors=errors,
            meets_quality_gate=passed,
            pareto_optimal=False,
        )
