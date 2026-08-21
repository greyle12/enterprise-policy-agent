from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.evaluation.retrieval_dataset import RetrievalDatasetError, load_retrieval_dataset
from app.evaluation.retrieval_models import (
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.evaluation.retrieval_reporting import write_retrieval_report
from app.evaluation.retrieval_runner import (
    DEFAULT_RETRIEVAL_CANDIDATE_K,
    RetrievalEvaluationRunner,
)
from app.evaluation.retrieval_runtime import (
    RetrievalJudgmentError,
    build_retrieval_evaluation_runtime,
)
from app.rag.embeddings import DEFAULT_BGE_MODEL_NAME
from app.rag.reranking import DEFAULT_BGE_RERANKER_MODEL_NAME

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATASET = _PROJECT_ROOT / "tests" / "evaluation" / "retrieval_test_cases.jsonl"
_DEFAULT_OUTPUT_DIRECTORY = _PROJECT_ROOT / "artifacts" / "evaluation"
_DEFAULT_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure Recall@K and MRR for Vector, BM25, Hybrid/RRF, and Reranker."
    )
    parser.add_argument(
        "--mode",
        choices=[item.value for item in RetrievalEvaluationMode],
        default=RetrievalEvaluationMode.OFFLINE.value,
        help="offline is deterministic and network-free; bge loads the real local model stack.",
    )
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument("--policy-dir", type=Path, default=_DEFAULT_POLICY_DIRECTORY)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--candidate-k", type=int, default=DEFAULT_RETRIEVAL_CANDIDATE_K)
    parser.add_argument("--minimum-recall-at-5", type=float, default=0.80)
    parser.add_argument("--minimum-mrr-at-5", type=float, default=0.80)
    parser.add_argument("--embedding-model", default=DEFAULT_BGE_MODEL_NAME)
    parser.add_argument("--reranker-model", default=DEFAULT_BGE_RERANKER_MODEL_NAME)
    parser.add_argument("--device", default=None)
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> int:
    dataset = load_retrieval_dataset(args.dataset)
    mode = RetrievalEvaluationMode(args.mode)
    thresholds = RetrievalEvaluationThresholds(
        minimum_recall=args.minimum_recall_at_5,
        minimum_mrr=args.minimum_mrr_at_5,
    )
    runtime = build_retrieval_evaluation_runtime(
        policy_directory=args.policy_dir,
        cases=dataset.cases,
        mode=mode,
        embedding_model=args.embedding_model,
        reranker_model=args.reranker_model,
        device=args.device,
        candidate_k=args.candidate_k,
    )
    report = RetrievalEvaluationRunner(
        retriever=runtime.retriever,
        evaluation_mode=mode,
        embedding_provider=runtime.embedding_provider,
        reranker_provider=runtime.reranker_provider,
        external_model_calls=runtime.external_model_calls,
        dataset_sha256=dataset.sha256,
        corpus_sha256=runtime.corpus_sha256,
        candidate_k=args.candidate_k,
        thresholds=thresholds,
    ).run(dataset.cases)
    paths = write_retrieval_report(report, args.output_dir)
    summary = {
        "quality_gate_passed": report.quality_gate_passed,
        "evaluation_mode": report.evaluation_mode.value,
        "total_cases": report.total_cases,
        "corpus_chunks": len(runtime.chunks),
        "metrics": {
            item.channel.value: {
                **{f"recall_at_{k}": item.recall_at_k[k] for k in report.ks},
                f"mrr_at_{report.thresholds.gate_k}": item.mrr_at_k,
                "meets_quality_gate": item.meets_quality_gate,
            }
            for item in report.summaries
        },
        "json_report": str(paths.json_path.resolve()),
        "markdown_report": str(paths.markdown_path.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.quality_gate_passed else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return _run(args)
    except (RetrievalDatasetError, RetrievalJudgmentError) as exc:
        print(f"检索评测数据无效：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must provide one stable failure boundary
        print(f"检索评测运行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
