from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.evaluation.retrieval_experiment_models import (
    CandidateWindowExperimentReport,
    CandidateWindowPoint,
)
from app.evaluation.retrieval_experiment_reporting import (
    render_candidate_window_markdown,
    write_candidate_window_report,
)
from app.evaluation.retrieval_models import (
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.performance.models import PerformanceEnvironment
from app.rag.policy_retriever import RetrievalMethod


def _report() -> CandidateWindowExperimentReport:
    point = CandidateWindowPoint(
        channel=RetrievalMethod.HYBRID,
        candidate_k=20,
        query_samples=20,
        recall_at_5=0.9,
        mrr_at_5=0.85,
        ndcg_at_5=0.88,
        minimum_ms=1.0,
        average_ms=2.0,
        p50_ms=2.0,
        p95_ms=3.0,
        maximum_ms=4.0,
        error_count=0,
        meets_quality_gate=True,
        pareto_optimal=True,
    )
    return CandidateWindowExperimentReport(
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="fixture",
        reranker_provider="fixture",
        requested_device=None,
        embedding_batch_size=32,
        reranker_batch_size=32,
        external_model_calls=False,
        model_download_may_be_required=False,
        generated_at=datetime(2026, 8, 24, tzinfo=UTC),
        dataset_sha256="a" * 64,
        corpus_sha256="b" * 64,
        total_cases=20,
        total_judgments=30,
        candidate_ks=(20,),
        default_candidate_k=20,
        warmup_iterations=1,
        measured_repetitions=3,
        thresholds=RetrievalEvaluationThresholds(),
        environment=PerformanceEnvironment(
            python_version="3.12.10",
            operating_system="Windows",
            machine="AMD64",
        ),
        experiment_completed=True,
        default_quality_gate_passed=True,
        quality_gate_passed=True,
        pareto_frontier={RetrievalMethod.HYBRID: (20,)},
        points=(point,),
    )


def test_markdown_explains_pareto_and_offline_boundary() -> None:
    markdown = render_candidate_window_markdown(_report())

    assert "Candidate Window" in markdown
    assert "Pareto" in markdown
    assert "Recall@5" in markdown
    assert "不代表真实 BGE" in markdown
    assert "报告不会修改生产配置" in markdown


def test_writes_machine_and_human_readable_reports(tmp_path: Path) -> None:
    paths = write_candidate_window_report(_report(), tmp_path)

    assert paths.json_path.is_file()
    assert paths.markdown_path.is_file()
    assert '"quality_gate_passed": true' in paths.json_path.read_text(encoding="utf-8")
