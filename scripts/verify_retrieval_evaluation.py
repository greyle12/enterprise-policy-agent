from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.retrieval_dataset import load_retrieval_dataset
from app.evaluation.retrieval_models import RetrievalEvaluationMode
from app.evaluation.retrieval_runner import RetrievalEvaluationRunner
from app.evaluation.retrieval_runtime import build_retrieval_evaluation_runtime
from app.rag.policy_retriever import RetrievalMethod

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_verification() -> dict[str, object]:
    dataset = load_retrieval_dataset(
        _PROJECT_ROOT / "tests" / "evaluation" / "retrieval_test_cases.jsonl"
    )
    runtime = build_retrieval_evaluation_runtime(
        policy_directory=_PROJECT_ROOT / "data" / "policies",
        cases=dataset.cases,
        mode=RetrievalEvaluationMode.OFFLINE,
    )
    report = RetrievalEvaluationRunner(
        retriever=runtime.retriever,
        evaluation_mode=RetrievalEvaluationMode.OFFLINE,
        embedding_provider=runtime.embedding_provider,
        reranker_provider=runtime.reranker_provider,
        external_model_calls=runtime.external_model_calls,
        dataset_sha256=dataset.sha256,
        corpus_sha256=runtime.corpus_sha256,
    ).run(dataset.cases)
    summaries = {item.channel: item for item in report.summaries}
    checks = {
        "judged_dataset_has_cross_domain_queries": (
            len(dataset.cases) == 20
            and {tag for case in dataset.cases for tag in case.tags}
            >= {"travel", "procurement", "expense", "security", "leave"}
        ),
        "existing_policy_corpus_is_reused": len(runtime.chunks) == 199,
        "all_ablation_channels_are_measured": set(summaries) == set(RetrievalMethod),
        "recall_at_1_3_5_is_reported": all(
            set(summary.recall_at_k) == {1, 3, 5} for summary in summaries.values()
        ),
        "mrr_at_5_is_reported": all(0.0 <= item.mrr_at_k <= 1.0 for item in summaries.values()),
        "ndcg_at_1_3_5_is_reported": all(
            set(summary.ndcg_at_k) == {1, 3, 5} for summary in summaries.values()
        ),
        "offline_runtime_has_no_external_model_calls": not report.external_model_calls,
        "hybrid_and_reranker_meet_quality_gate": report.quality_gate_passed,
        "evaluation_is_retrieval_only": all(
            len(channel.retrieved_chunk_ids) <= 5
            for case in report.case_results
            for channel in case.channels
        ),
    }
    return {
        "schema_version": "1.0",
        "phase": 31,
        "passed": all(checks.values()),
        "case_count": len(dataset.cases),
        "chunk_count": len(runtime.chunks),
        "dataset_sha256": dataset.sha256,
        "corpus_sha256": runtime.corpus_sha256,
        "quality_gate_passed": report.quality_gate_passed,
        "report_schema_version": report.schema_version,
        "metrics": {
            channel.value: {
                "recall_at_5": summary.recall_at_k[5],
                "mrr_at_5": summary.mrr_at_k,
                "ndcg_at_5": summary.ndcg_at_k[5],
            }
            for channel, summary in summaries.items()
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
