from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.evaluation.pgvector_ann_experiments import (
    PgvectorAnnExperimentRunner,
    PreparedHnswTarget,
)
from app.evaluation.pgvector_ann_models import HnswConfiguration
from app.evaluation.pgvector_ann_reporting import write_pgvector_ann_report
from app.evaluation.retrieval_dataset import load_retrieval_dataset
from app.evaluation.retrieval_models import RetrievalEvaluationMode

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _JudgmentTarget:
    def __init__(self, rankings: dict[str, tuple[str, ...]]) -> None:
        self._rankings = rankings

    def search(self, query: str, *, top_k: int = 5):
        return [
            SimpleNamespace(chunk=SimpleNamespace(chunk_id=value))
            for value in self._rankings[query][:top_k]
        ]


def run_verification(*, output_directory: Path | None = None) -> dict[str, object]:
    dataset = load_retrieval_dataset(
        _PROJECT_ROOT / "tests" / "evaluation" / "retrieval_test_cases.jsonl"
    )
    exact_rankings = {
        case.query: tuple(judgment.chunk_id for judgment in case.judgments[:5])
        for case in dataset.cases
    }
    low_config = HnswConfiguration(m=8, ef_construction=32, ef_search=20)
    default_config = HnswConfiguration(m=16, ef_construction=64, ef_search=40)
    report = PgvectorAnnExperimentRunner(
        exact_target=_JudgmentTarget(exact_rankings),
        hnsw_targets=(
            PreparedHnswTarget(low_config, _JudgmentTarget(exact_rankings), 4.0),
            PreparedHnswTarget(default_config, _JudgmentTarget(exact_rankings), 6.0),
        ),
        default_configuration=default_config,
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider="deterministic_pgvector_ann_fixture_v1",
        source_collection="phase34-offline-fixture-3d",
        requested_device=None,
        embedding_batch_size=32,
        external_model_calls=False,
        dataset_sha256=dataset.sha256,
        corpus_sha256="a" * 64,
        warmup_iterations=0,
        measured_repetitions=1,
    ).run(dataset.cases)
    checks = {
        "exact_and_hnsw_points_are_compared": len(report.points) == 3,
        "ann_and_judged_metrics_are_separate": all(
            point.ann_recall_at_5 == 1.0
            and point.judged_recall_at_5 == 1.0
            and point.ndcg_at_5 == 1.0
            for point in report.points
        ),
        "authorization_is_materialized_before_hnsw": (
            report.security_boundary == "materialize_authorized_scope_before_hnsw"
        ),
        "index_build_and_query_latency_are_separate": all(
            point.index_build_ms is not None for point in report.points if point.backend == "hnsw"
        ),
        "default_configuration_passes": report.default_configuration_passed,
        "pareto_frontier_is_present": bool(report.pareto_configurations),
        "offline_verifier_uses_no_database_or_models": (
            not report.external_model_calls and not report.model_download_may_be_required
        ),
    }
    result: dict[str, object] = {
        "schema_version": "1.0",
        "phase": 34,
        "passed": all(checks.values()),
        "case_count": report.total_cases,
        "judgment_count": report.total_judgments,
        "point_count": len(report.points),
        "default_configuration": report.default_configuration.identity,
        "quality_gate_passed": report.quality_gate_passed,
        "database_calls": False,
        "external_model_calls": False,
        "checks": checks,
    }
    if output_directory is not None:
        paths = write_pgvector_ann_report(report, output_directory)
        result["json_report"] = str(paths.json_path.resolve())
        result["markdown_report"] = str(paths.markdown_path.resolve())
    return result


def main() -> int:
    result = run_verification(output_directory=_PROJECT_ROOT / "artifacts" / "evaluation")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
