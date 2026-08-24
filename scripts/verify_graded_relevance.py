from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.retrieval_dataset import load_retrieval_dataset
from app.evaluation.retrieval_models import RelevanceGrade
from app.evaluation.retrieval_runner import ndcg_at_k
from scripts.verify_retrieval_evaluation import run_verification as verify_phase31

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_verification() -> dict[str, object]:
    """Verify the Phase 32 graded-judgment and nDCG quality contract offline."""

    dataset = load_retrieval_dataset(
        _PROJECT_ROOT / "tests" / "evaluation" / "retrieval_test_cases.jsonl"
    )
    phase31 = verify_phase31()
    observed_grades = {judgment.relevance for case in dataset.cases for judgment in case.judgments}
    ideal_ndcg = ndcg_at_k(
        ("direct", "supporting", "marginal"),
        {"direct": 3, "supporting": 2, "marginal": 1},
        k=3,
    )
    reversed_ndcg = ndcg_at_k(
        ("marginal", "supporting", "direct"),
        {"direct": 3, "supporting": 2, "marginal": 1},
        k=3,
    )
    production_metrics = {
        channel: metrics
        for channel, metrics in phase31["metrics"].items()
        if channel in {"hybrid", "reranked"}
    }
    checks = {
        "phase31_retrieval_contract_still_passes": phase31["passed"] is True,
        "dataset_uses_all_three_relevance_grades": observed_grades == set(RelevanceGrade),
        "every_judgment_has_a_rationale": all(
            judgment.rationale for case in dataset.cases for judgment in case.judgments
        ),
        "binary_relevant_ids_are_derived_from_judgments": all(
            case.relevant_chunk_ids == tuple(judgment.chunk_id for judgment in case.judgments)
            for case in dataset.cases
        ),
        "ideal_ranking_has_perfect_ndcg": ideal_ndcg == 1.0,
        "reversed_grades_are_discounted": 0.0 < reversed_ndcg < ideal_ndcg,
        "report_schema_is_v2": phase31["report_schema_version"] == "2.0",
        "production_channels_meet_ndcg_gate": all(
            metrics["ndcg_at_5"] >= 0.80 for metrics in production_metrics.values()
        ),
    }
    return {
        "schema_version": "1.0",
        "phase": 32,
        "passed": all(checks.values()),
        "case_count": len(dataset.cases),
        "judgment_count": sum(len(case.judgments) for case in dataset.cases),
        "observed_relevance_grades": sorted(int(grade) for grade in observed_grades),
        "ideal_ndcg_at_3": ideal_ndcg,
        "reversed_ndcg_at_3": reversed_ndcg,
        "quality_gate_passed": phase31["quality_gate_passed"],
        "metrics": production_metrics,
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
