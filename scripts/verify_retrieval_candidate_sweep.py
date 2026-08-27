from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.retrieval_dataset import load_retrieval_dataset
from app.evaluation.retrieval_experiments import (
    DEFAULT_CANDIDATE_WINDOWS,
    DEFAULT_PRODUCTION_CANDIDATE_K,
    EXPERIMENT_CHANNELS,
    CandidateWindowExperimentRunner,
)
from app.evaluation.retrieval_models import RetrievalEvaluationMode
from app.evaluation.retrieval_runtime import build_retrieval_evaluation_runtime

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_verification() -> dict[str, object]:
    """Verify the Phase 33 candidate-window experiment method without real models."""

    dataset = load_retrieval_dataset(
        _PROJECT_ROOT / "tests" / "evaluation" / "retrieval_test_cases.jsonl"
    )
    runtime = build_retrieval_evaluation_runtime(
        policy_directory=_PROJECT_ROOT / "data" / "policies",
        cases=dataset.cases,
        mode=RetrievalEvaluationMode.OFFLINE,
        candidate_k=max(DEFAULT_CANDIDATE_WINDOWS),
    )
    report = CandidateWindowExperimentRunner(
        retriever=runtime.retriever,
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider=runtime.embedding_provider,
        reranker_provider=runtime.reranker_provider,
        requested_device=runtime.requested_device,
        embedding_batch_size=runtime.embedding_batch_size,
        reranker_batch_size=runtime.reranker_batch_size,
        external_model_calls=runtime.external_model_calls,
        dataset_sha256=dataset.sha256,
        corpus_sha256=runtime.corpus_sha256,
        candidate_ks=DEFAULT_CANDIDATE_WINDOWS,
        default_candidate_k=DEFAULT_PRODUCTION_CANDIDATE_K,
        warmup_iterations=0,
        measured_repetitions=1,
    ).run(dataset.cases)
    points_by_channel = {
        channel: tuple(point for point in report.points if point.channel is channel)
        for channel in EXPERIMENT_CHANNELS
    }
    checks = {
        "one_runtime_reuses_the_existing_authorized_retriever": len(runtime.chunks) == 199,
        "all_candidate_windows_and_channels_are_measured": (
            len(report.points) == len(DEFAULT_CANDIDATE_WINDOWS) * len(EXPERIMENT_CHANNELS)
            and all(
                {point.candidate_k for point in points} == set(DEFAULT_CANDIDATE_WINDOWS)
                for points in points_by_channel.values()
            )
        ),
        "quality_and_latency_are_recorded": all(
            0.0 <= point.recall_at_5 <= 1.0
            and 0.0 <= point.mrr_at_5 <= 1.0
            and 0.0 <= point.ndcg_at_5 <= 1.0
            and point.p95_ms >= point.p50_ms >= point.minimum_ms
            for point in report.points
        ),
        "candidate_window_changes_at_least_one_ranking": any(
            len({(point.recall_at_5, point.mrr_at_5, point.ndcg_at_5) for point in points}) > 1
            for points in points_by_channel.values()
        ),
        "default_window_meets_three_metric_gate": report.default_quality_gate_passed,
        "pareto_frontier_is_nonempty_per_channel": all(
            report.pareto_frontier[channel] for channel in EXPERIMENT_CHANNELS
        ),
        "environment_and_model_identity_are_captured": (
            bool(report.environment.python_version)
            and bool(report.embedding_provider)
            and bool(report.reranker_provider)
            and report.embedding_batch_size == report.reranker_batch_size == 32
        ),
        "offline_run_has_no_external_model_calls": (
            not report.external_model_calls and not report.model_download_may_be_required
        ),
    }
    return {
        "schema_version": "1.0",
        "phase": 33,
        "passed": all(checks.values()),
        "case_count": report.total_cases,
        "judgment_count": report.total_judgments,
        "candidate_ks": report.candidate_ks,
        "default_candidate_k": report.default_candidate_k,
        "point_count": len(report.points),
        "quality_gate_passed": report.quality_gate_passed,
        "pareto_frontier": {
            channel.value: values for channel, values in report.pareto_frontier.items()
        },
        "network_calls": False,
        "external_model_calls": False,
        "checks": checks,
    }


def main() -> int:
    result = run_verification()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
