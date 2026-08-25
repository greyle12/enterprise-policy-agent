from __future__ import annotations

from datetime import UTC, datetime

from app.evaluation.pgvector_ann_models import (
    HnswConfiguration,
    PgvectorAnnExperimentReport,
    PgvectorAnnPoint,
)
from app.evaluation.pgvector_ann_reporting import (
    render_pgvector_ann_markdown,
    write_pgvector_ann_report,
)
from app.evaluation.retrieval_models import (
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.performance.models import PerformanceEnvironment


def _report() -> PgvectorAnnExperimentReport:
    config = HnswConfiguration(m=16, ef_construction=64, ef_search=40)
    point = PgvectorAnnPoint(
        backend="hnsw",
        configuration=config,
        query_samples=20,
        index_build_ms=10.0,
        ann_recall_at_5=1.0,
        judged_recall_at_5=0.9,
        mrr_at_5=0.9,
        ndcg_at_5=0.9,
        minimum_ms=1.0,
        average_ms=2.0,
        p50_ms=2.0,
        p95_ms=3.0,
        maximum_ms=4.0,
        error_count=0,
        meets_quality_gate=True,
        pareto_optimal=True,
    )
    return PgvectorAnnExperimentReport(
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="fixture",
        source_collection="experiment-offline-3d",
        requested_device=None,
        embedding_batch_size=32,
        external_model_calls=False,
        model_download_may_be_required=False,
        generated_at=datetime(2026, 8, 24, tzinfo=UTC),
        dataset_sha256="a" * 64,
        corpus_sha256="b" * 64,
        total_cases=20,
        total_judgments=30,
        warmup_iterations=1,
        measured_repetitions=3,
        minimum_ann_recall_at_5=0.95,
        thresholds=RetrievalEvaluationThresholds(),
        default_configuration=config,
        environment=PerformanceEnvironment(
            python_version="3.12.10",
            operating_system="Windows",
            machine="AMD64",
        ),
        experiment_completed=True,
        exact_baseline_passed=True,
        default_configuration_passed=True,
        quality_gate_passed=True,
        pareto_configurations=(config.identity,),
        points=(point,),
    )


def test_markdown_explains_ann_judgments_and_security_boundary() -> None:
    markdown = render_pgvector_ann_markdown(_report())

    assert "ANN Recall@5" in markdown
    assert "Judged Recall@5" in markdown
    assert "Errors" in markdown
    assert "授权 ID 先复制" in markdown
    assert "不会自动修改生产配置" in markdown


def test_writes_pgvector_ann_reports(tmp_path) -> None:
    paths = write_pgvector_ann_report(_report(), tmp_path)

    assert paths.json_path.is_file()
    assert paths.markdown_path.is_file()
    assert '"quality_gate_passed": true' in paths.json_path.read_text(encoding="utf-8")
