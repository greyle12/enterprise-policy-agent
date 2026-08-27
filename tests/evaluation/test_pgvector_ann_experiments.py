from __future__ import annotations

from types import SimpleNamespace

from app.evaluation.pgvector_ann_experiments import (
    PgvectorAnnExperimentRunner,
    PreparedHnswTarget,
)
from app.evaluation.pgvector_ann_models import HnswConfiguration
from app.evaluation.retrieval_models import RetrievalCase, RetrievalEvaluationMode


def _results(*chunk_ids: str):
    return [SimpleNamespace(chunk=SimpleNamespace(chunk_id=value)) for value in chunk_ids]


class _Target:
    def __init__(self, ranked_ids: tuple[str, ...]) -> None:
        self.ranked_ids = ranked_ids
        self.calls = 0

    def search(self, query: str, *, top_k: int = 5):
        self.calls += 1
        return _results(*self.ranked_ids[:top_k])


class _FailingTarget:
    def search(self, query: str, *, top_k: int = 5):
        raise RuntimeError("database query failed")


def _case() -> RetrievalCase:
    return RetrievalCase(
        case_id="RET-001",
        title="ANN",
        query="find direct evidence",
        relevant_chunk_ids=("direct",),
    )


def test_runner_compares_hnsw_to_exact_and_excludes_warmups() -> None:
    exact = _Target(("direct", "support", "other"))
    default = HnswConfiguration(m=16, ef_construction=64, ef_search=40)
    hnsw = _Target(("direct", "support", "other"))
    report = PgvectorAnnExperimentRunner(
        exact_target=exact,
        hnsw_targets=(PreparedHnswTarget(default, hnsw, 12.5),),
        default_configuration=default,
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="fixture",
        source_collection="experiment-offline-3d",
        requested_device=None,
        embedding_batch_size=32,
        external_model_calls=False,
        dataset_sha256="a" * 64,
        corpus_sha256="b" * 64,
        warmup_iterations=1,
        measured_repetitions=2,
    ).run([_case()])

    assert report.quality_gate_passed is True
    assert report.security_boundary == "materialize_authorized_scope_before_hnsw"
    assert len(report.points) == 2
    assert all(point.query_samples == 2 for point in report.points)
    assert report.points[1].ann_recall_at_5 == 1.0
    assert report.points[1].index_build_ms == 12.5
    assert exact.calls == hnsw.calls == 3


def test_default_hnsw_fails_when_ann_drops_exact_neighbors() -> None:
    exact = _Target(("direct", "support", "other", "fourth", "fifth"))
    default = HnswConfiguration(m=8, ef_construction=32, ef_search=1)
    approximate = _Target(("direct", "miss-1", "miss-2", "miss-3", "miss-4"))
    report = PgvectorAnnExperimentRunner(
        exact_target=exact,
        hnsw_targets=(PreparedHnswTarget(default, approximate, 1.0),),
        default_configuration=default,
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="fixture",
        source_collection="experiment-offline-3d",
        requested_device=None,
        embedding_batch_size=1,
        external_model_calls=False,
        dataset_sha256="a" * 64,
        corpus_sha256="b" * 64,
        warmup_iterations=0,
        measured_repetitions=1,
        minimum_ann_recall_at_5=0.95,
    ).run([_case()])

    assert report.exact_baseline_passed is True
    assert report.default_configuration_passed is False
    assert report.quality_gate_passed is False
    assert report.points[1].ann_recall_at_5 == 0.2


def test_runner_exposes_exact_query_errors_instead_of_reporting_false_quality() -> None:
    default = HnswConfiguration(m=16, ef_construction=64, ef_search=40)
    report = PgvectorAnnExperimentRunner(
        exact_target=_FailingTarget(),
        hnsw_targets=(PreparedHnswTarget(default, _Target(("direct",)), 1.0),),
        default_configuration=default,
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="fixture",
        source_collection="experiment-offline-3d",
        requested_device=None,
        embedding_batch_size=1,
        external_model_calls=False,
        dataset_sha256="a" * 64,
        corpus_sha256="b" * 64,
        warmup_iterations=0,
        measured_repetitions=1,
    ).run([_case()])

    exact = report.points[0]
    assert exact.ann_recall_at_5 == 0.0
    assert exact.error_count == 1
    assert exact.errors == ("RuntimeError: database query failed",)
    assert report.experiment_completed is False
    assert report.quality_gate_passed is False
