from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.evaluation.retrieval_experiment_models import CandidateWindowPoint
from app.evaluation.retrieval_experiments import (
    CandidateWindowExperimentRunner,
    mark_pareto_frontier,
    normalize_candidate_windows,
)
from app.evaluation.retrieval_models import (
    RetrievalCase,
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.rag.policy_retriever import RetrievalMethod

_DIGEST = "a" * 64


def _results(*chunk_ids: str):
    return [SimpleNamespace(chunk=SimpleNamespace(chunk_id=value)) for value in chunk_ids]


class _WindowAwareRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple[RetrievalMethod, int]] = []

    def _search(self, channel: RetrievalMethod, candidate_k: int):
        self.calls.append((channel, candidate_k))
        return _results("direct") if candidate_k == 20 else _results("miss")

    def search_hybrid(self, query: str, *, top_k: int = 5, candidate_k=None):
        return self._search(RetrievalMethod.HYBRID, candidate_k)[:top_k]

    def search_reranked(self, query: str, *, top_k: int = 5, candidate_k=None):
        return self._search(RetrievalMethod.RERANKED, candidate_k)[:top_k]


def _case() -> RetrievalCase:
    return RetrievalCase(
        case_id="RET-001",
        title="candidate window",
        query="find the direct evidence",
        relevant_chunk_ids=("direct",),
    )


def _point(
    *,
    candidate_k: int,
    recall: float,
    mrr: float,
    ndcg: float,
    p95_ms: float,
) -> CandidateWindowPoint:
    return CandidateWindowPoint(
        channel=RetrievalMethod.HYBRID,
        candidate_k=candidate_k,
        query_samples=1,
        recall_at_5=recall,
        mrr_at_5=mrr,
        ndcg_at_5=ndcg,
        minimum_ms=p95_ms,
        average_ms=p95_ms,
        p50_ms=p95_ms,
        p95_ms=p95_ms,
        maximum_ms=p95_ms,
        error_count=0,
        meets_quality_gate=True,
        pareto_optimal=False,
    )


def test_experiment_sweeps_one_retriever_and_gates_only_the_default_window() -> None:
    retriever = _WindowAwareRetriever()
    report = CandidateWindowExperimentRunner(
        retriever=retriever,
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="fixture-embedding",
        reranker_provider="fixture-reranker",
        requested_device=None,
        embedding_batch_size=16,
        reranker_batch_size=8,
        external_model_calls=False,
        dataset_sha256=_DIGEST,
        corpus_sha256=_DIGEST,
        candidate_ks=(5, 20),
        default_candidate_k=20,
        warmup_iterations=1,
        measured_repetitions=2,
        thresholds=RetrievalEvaluationThresholds(
            minimum_recall=1.0,
            minimum_mrr=1.0,
            minimum_ndcg=1.0,
        ),
    ).run([_case()])

    assert report.quality_gate_passed is True
    assert report.default_quality_gate_passed is True
    assert len(report.points) == 4
    assert all(point.query_samples == 2 for point in report.points)
    assert all(not point.meets_quality_gate for point in report.points if point.candidate_k == 5)
    assert all(point.meets_quality_gate for point in report.points if point.candidate_k == 20)
    assert len(retriever.calls) == 12
    assert report.final_top_k == 5
    assert report.external_model_calls is False


def test_candidate_windows_are_sorted_deduplicated_and_preflighted() -> None:
    assert normalize_candidate_windows((20, 5, 20, 10), default_candidate_k=20) == (
        5,
        10,
        20,
    )

    with pytest.raises(ValueError, match="included"):
        normalize_candidate_windows((5, 10), default_candidate_k=20)
    with pytest.raises(ValueError, match="five to one thousand"):
        normalize_candidate_windows((4, 20), default_candidate_k=20)
    with pytest.raises(ValueError, match="five to one thousand"):
        normalize_candidate_windows((True, 20), default_candidate_k=20)


def test_pareto_frontier_marks_only_non_dominated_points() -> None:
    points = mark_pareto_frontier(
        (
            _point(candidate_k=5, recall=0.8, mrr=0.8, ndcg=0.8, p95_ms=10),
            _point(candidate_k=10, recall=0.8, mrr=0.8, ndcg=0.8, p95_ms=12),
            _point(candidate_k=20, recall=0.9, mrr=0.9, ndcg=0.9, p95_ms=20),
        )
    )

    assert {point.candidate_k for point in points if point.pareto_optimal} == {5, 20}


def test_experiment_requires_a_top_five_quality_gate() -> None:
    with pytest.raises(ValueError, match="gate_k=5"):
        CandidateWindowExperimentRunner(
            retriever=_WindowAwareRetriever(),
            evaluation_mode=RetrievalEvaluationMode.OFFLINE,
            embedding_provider="fixture",
            reranker_provider="fixture",
            requested_device=None,
            embedding_batch_size=1,
            reranker_batch_size=1,
            external_model_calls=False,
            dataset_sha256=_DIGEST,
            corpus_sha256=_DIGEST,
            candidate_ks=(5, 20),
            thresholds=RetrievalEvaluationThresholds(gate_k=3),
        )
