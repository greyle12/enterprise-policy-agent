from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.evaluation.retrieval_models import (
    RetrievalCaseChannelResult,
    RetrievalCaseResult,
    RetrievalChannelSummary,
    RetrievalEvaluationMode,
    RetrievalEvaluationReport,
    RetrievalEvaluationThresholds,
    RelevanceJudgment,
)
from app.evaluation.retrieval_reporting import (
    render_retrieval_markdown,
    write_retrieval_report,
)
from app.rag.policy_retriever import RetrievalMethod


def _report() -> RetrievalEvaluationReport:
    measurement = RetrievalCaseChannelResult(
        channel=RetrievalMethod.HYBRID,
        retrieved_chunk_ids=("chunk-1",),
        recall_at_k={1: 1.0, 3: 1.0, 5: 1.0},
        ndcg_at_k={1: 1.0, 3: 1.0, 5: 1.0},
        first_relevant_rank=1,
        reciprocal_rank=1.0,
        duration_ms=1.25,
    )
    return RetrievalEvaluationReport(
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="fixture",
        reranker_provider="fixture",
        external_model_calls=False,
        generated_at=datetime(2026, 8, 20, tzinfo=UTC),
        dataset_sha256="a" * 64,
        corpus_sha256="b" * 64,
        total_cases=1,
        channels=(RetrievalMethod.HYBRID,),
        ks=(1, 3, 5),
        candidate_k=20,
        thresholds=RetrievalEvaluationThresholds(required_channels=(RetrievalMethod.HYBRID,)),
        quality_gate_passed=True,
        summaries=(
            RetrievalChannelSummary(
                channel=RetrievalMethod.HYBRID,
                case_count=1,
                recall_at_k={1: 1.0, 3: 1.0, 5: 1.0},
                mrr_at_k=1.0,
                ndcg_at_k={1: 1.0, 3: 1.0, 5: 1.0},
                average_duration_ms=1.25,
                error_count=0,
                meets_quality_gate=True,
            ),
        ),
        case_results=(
            RetrievalCaseResult(
                case_id="RET-001",
                title="case",
                query="query",
                relevant_chunk_ids=("chunk-1",),
                judgments=(
                    RelevanceJudgment(
                        chunk_id="chunk-1",
                        relevance=3,
                        rationale="direct answer",
                    ),
                ),
                channels=(measurement,),
            ),
        ),
    )


def test_markdown_explains_offline_limit_and_metrics() -> None:
    report = _report()
    markdown = render_retrieval_markdown(report)

    assert report.schema_version == "2.0"
    assert "Recall@5" in markdown
    assert "MRR@5" in markdown
    assert "nDCG@5" in markdown
    assert "(G3)" in markdown
    assert "不代表真实 BGE" in markdown


def test_writes_json_and_markdown_reports(tmp_path: Path) -> None:
    paths = write_retrieval_report(_report(), tmp_path)

    assert paths.json_path.is_file()
    assert paths.markdown_path.is_file()
    assert '"quality_gate_passed": true' in paths.json_path.read_text(encoding="utf-8")
