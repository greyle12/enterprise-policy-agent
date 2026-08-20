from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.evaluation.retrieval_models import (
    RetrievalCase,
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.evaluation.retrieval_runner import (
    RetrievalEvaluationRunner,
    recall_at_k,
    reciprocal_rank,
)
from app.rag.policy_retriever import RetrievalMethod

_DIGEST = "a" * 64


def _results(*chunk_ids: str):
    return [SimpleNamespace(chunk=SimpleNamespace(chunk_id=chunk_id)) for chunk_id in chunk_ids]


class _FakeRetriever:
    def search(self, query: str, *, top_k: int = 5):
        return _results("miss", "rel-1", "rel-2")[:top_k]

    def search_keywords(self, query: str, *, top_k: int = 5):
        return _results("rel-1", "miss", "rel-2")[:top_k]

    def search_hybrid(self, query: str, *, top_k: int = 5, candidate_k=None):
        assert candidate_k == 20
        return _results("rel-1", "rel-2", "miss")[:top_k]

    def search_reranked(self, query: str, *, top_k: int = 5, candidate_k=None):
        assert candidate_k == 20
        return _results("rel-2", "rel-1", "miss")[:top_k]


def _case() -> RetrievalCase:
    return RetrievalCase(
        case_id="RET-001",
        title="two labels",
        query="find both labels",
        relevant_chunk_ids=("rel-1", "rel-2"),
    )


def test_metric_functions_support_multiple_relevant_chunks() -> None:
    ranked = ("miss", "rel-2", "rel-1")

    assert recall_at_k(ranked, ("rel-1", "rel-2"), k=1) == 0.0
    assert recall_at_k(ranked, ("rel-1", "rel-2"), k=2) == 0.5
    assert recall_at_k(ranked, ("rel-1", "rel-2"), k=3) == 1.0
    assert reciprocal_rank(ranked, ("rel-1", "rel-2"), k=3) == (2, 0.5)


def test_runner_reports_all_ablation_channels_and_macro_metrics() -> None:
    report = RetrievalEvaluationRunner(
        retriever=_FakeRetriever(),
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="fixture",
        reranker_provider="fixture",
        external_model_calls=False,
        dataset_sha256=_DIGEST,
        corpus_sha256=_DIGEST,
        thresholds=RetrievalEvaluationThresholds(minimum_recall=1.0, minimum_mrr=1.0),
        clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
    ).run([_case()])

    assert report.quality_gate_passed is True
    assert report.channels == tuple(RetrievalMethod)
    assert report.generated_at == datetime(2026, 8, 20, tzinfo=UTC)
    assert {summary.channel for summary in report.summaries} == set(RetrievalMethod)
    assert report.summaries[0].recall_at_k == {1: 0.0, 3: 1.0, 5: 1.0}
    assert report.summaries[0].mrr_at_k == 0.5
    assert report.summaries[2].meets_quality_gate is True


def test_runner_records_channel_exception_and_fails_required_gate() -> None:
    class FailingRetriever(_FakeRetriever):
        def search_reranked(self, query: str, *, top_k: int = 5, candidate_k=None):
            raise RuntimeError("provider unavailable")

    report = RetrievalEvaluationRunner(
        retriever=FailingRetriever(),
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="fixture",
        reranker_provider="fixture",
        external_model_calls=False,
        dataset_sha256=_DIGEST,
        corpus_sha256=_DIGEST,
    ).run([_case()])

    assert report.quality_gate_passed is False
    reranked = report.case_results[0].channels[-1]
    assert reranked.error == "RuntimeError: provider unavailable"
    assert reranked.reciprocal_rank == 0.0


def test_runner_rejects_gate_k_missing_from_measurements() -> None:
    with pytest.raises(ValueError, match="gate_k must be present"):
        RetrievalEvaluationRunner(
            retriever=_FakeRetriever(),
            evaluation_mode=RetrievalEvaluationMode.OFFLINE,
            embedding_provider="fixture",
            reranker_provider="fixture",
            external_model_calls=False,
            dataset_sha256=_DIGEST,
            corpus_sha256=_DIGEST,
            ks=(1, 3),
        )
