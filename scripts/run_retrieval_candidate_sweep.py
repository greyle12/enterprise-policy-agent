from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.evaluation.retrieval_dataset import RetrievalDatasetError, load_retrieval_dataset
from app.evaluation.retrieval_experiment_reporting import write_candidate_window_report
from app.evaluation.retrieval_experiments import (
    DEFAULT_CANDIDATE_WINDOWS,
    DEFAULT_PRODUCTION_CANDIDATE_K,
    CandidateWindowExperimentRunner,
    normalize_candidate_windows,
)
from app.evaluation.retrieval_models import (
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.evaluation.retrieval_runtime import (
    RetrievalJudgmentError,
    build_retrieval_evaluation_runtime,
)
from app.rag.embeddings import DEFAULT_BGE_MODEL_NAME
from app.rag.reranking import DEFAULT_BGE_RERANKER_MODEL_NAME

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATASET = _PROJECT_ROOT / "tests" / "evaluation" / "retrieval_test_cases.jsonl"
_DEFAULT_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_DEFAULT_OUTPUT_DIRECTORY = _PROJECT_ROOT / "artifacts" / "evaluation"


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _non_negative_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep Hybrid/Reranker candidate windows and report Recall/MRR/nDCG with p50/p95."
        )
    )
    parser.add_argument(
        "--mode",
        choices=[item.value for item in RetrievalEvaluationMode],
        default=RetrievalEvaluationMode.OFFLINE.value,
    )
    parser.add_argument(
        "--candidate-k",
        nargs="+",
        type=_positive_integer,
        default=list(DEFAULT_CANDIDATE_WINDOWS),
        help="Candidate windows to compare, for example: --candidate-k 5 10 20 40",
    )
    parser.add_argument(
        "--default-candidate-k",
        type=_positive_integer,
        default=DEFAULT_PRODUCTION_CANDIDATE_K,
    )
    parser.add_argument("--warmups", type=_non_negative_integer, default=1)
    parser.add_argument("--repetitions", type=_positive_integer, default=3)
    parser.add_argument("--minimum-recall-at-5", type=float, default=0.80)
    parser.add_argument("--minimum-mrr-at-5", type=float, default=0.80)
    parser.add_argument("--minimum-ndcg-at-5", type=float, default=0.80)
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument("--policy-dir", type=Path, default=_DEFAULT_POLICY_DIRECTORY)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--embedding-model", default=DEFAULT_BGE_MODEL_NAME)
    parser.add_argument("--reranker-model", default=DEFAULT_BGE_RERANKER_MODEL_NAME)
    parser.add_argument("--device", default=None)
    parser.add_argument("--embedding-batch-size", type=_positive_integer, default=32)
    parser.add_argument("--reranker-batch-size", type=_positive_integer, default=32)
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    dataset = load_retrieval_dataset(args.dataset)
    mode = RetrievalEvaluationMode(args.mode)
    candidate_ks = normalize_candidate_windows(
        args.candidate_k,
        default_candidate_k=args.default_candidate_k,
    )
    runtime = build_retrieval_evaluation_runtime(
        policy_directory=args.policy_dir,
        cases=dataset.cases,
        mode=mode,
        embedding_model=args.embedding_model,
        reranker_model=args.reranker_model,
        device=args.device,
        embedding_batch_size=args.embedding_batch_size,
        reranker_batch_size=args.reranker_batch_size,
        candidate_k=max(candidate_ks),
    )
    report = CandidateWindowExperimentRunner(
        retriever=runtime.retriever,
        evaluation_mode=mode,
        embedding_provider=runtime.embedding_provider,
        reranker_provider=runtime.reranker_provider,
        requested_device=runtime.requested_device,
        embedding_batch_size=runtime.embedding_batch_size,
        reranker_batch_size=runtime.reranker_batch_size,
        external_model_calls=runtime.external_model_calls,
        dataset_sha256=dataset.sha256,
        corpus_sha256=runtime.corpus_sha256,
        candidate_ks=candidate_ks,
        default_candidate_k=args.default_candidate_k,
        warmup_iterations=args.warmups,
        measured_repetitions=args.repetitions,
        thresholds=RetrievalEvaluationThresholds(
            minimum_recall=args.minimum_recall_at_5,
            minimum_mrr=args.minimum_mrr_at_5,
            minimum_ndcg=args.minimum_ndcg_at_5,
        ),
    ).run(dataset.cases)
    paths = write_candidate_window_report(report, args.output_dir)
    summary = {
        "quality_gate_passed": report.quality_gate_passed,
        "evaluation_mode": report.evaluation_mode.value,
        "candidate_ks": report.candidate_ks,
        "default_candidate_k": report.default_candidate_k,
        "warmup_iterations": report.warmup_iterations,
        "measured_repetitions": report.measured_repetitions,
        "external_model_calls": report.external_model_calls,
        "pareto_frontier": {
            channel.value: values for channel, values in report.pareto_frontier.items()
        },
        "points": [
            {
                "channel": point.channel.value,
                "candidate_k": point.candidate_k,
                "recall_at_5": point.recall_at_5,
                "mrr_at_5": point.mrr_at_5,
                "ndcg_at_5": point.ndcg_at_5,
                "p50_ms": round(point.p50_ms, 3),
                "p95_ms": round(point.p95_ms, 3),
                "meets_quality_gate": point.meets_quality_gate,
                "pareto_optimal": point.pareto_optimal,
            }
            for point in report.points
        ],
        "json_report": str(paths.json_path.resolve()),
        "markdown_report": str(paths.markdown_path.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.quality_gate_passed else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return _run(args)
    except (RetrievalDatasetError, RetrievalJudgmentError, ValueError) as exc:
        print(f"候选窗口实验参数或数据无效：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - stable CLI failure boundary
        print(f"候选窗口实验失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
