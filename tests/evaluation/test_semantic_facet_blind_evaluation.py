import json
from pathlib import Path

from scripts.evaluate_semantic_request_facets import load_prediction_dataset, evaluate_predictions
from scripts.semantic_request_facets_adapter import load_semantic_request_facet_bundle

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs/gate_v3/semantic-facet-blind-v2-confirmed"


def _bundle():
    return load_semantic_request_facet_bundle(
        BUNDLE / "records.jsonl",
        BUNDLE / "accepted.json",
        BUNDLE / "confirmation.json",
        manifest_path=BUNDLE / "manifest.json",
        project_root=ROOT,
    )


def _prediction_rows(bundle):
    return [
        {
            "schema_version": "1.0",
            "case_id": case.case_id,
            "predicted_facets": [
                {"facet": facet.facet, "binding": facet.binding} for facet in case.accepted_facets
            ],
        }
        for case in bundle.cases
    ]


def _evaluate(tmp_path, rows):
    path = tmp_path / "predictions.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return evaluate_predictions(_bundle(), load_prediction_dataset(path))


def test_empty_truth_is_exact_and_missing_output_is_preserved(tmp_path):
    result = _evaluate(tmp_path, _prediction_rows(_bundle()))
    assert result["metrics"]["case_exact_match"] == {
        "matched_count": 18,
        "total_case_count": 18,
        "rate": 1.0,
    }
    assert result["metrics"]["prediction_coverage"]["rate"] == 1.0
    assert result["metrics"]["facet_label"]["true_positive"] == 17
    assert result["metrics"]["facet_label"]["false_positive"] == 0
    assert result["metrics"]["binding"]["accuracy"] == 1.0


def test_extra_facet_on_empty_truth_is_false_positive(tmp_path):
    rows = _prediction_rows(_bundle())
    rows[8]["predicted_facets"] = [{"facet": "DEADLINE", "binding": "BOUND"}]
    result = _evaluate(tmp_path, rows)
    assert result["metrics"]["facet_label"]["false_positive"] == 1
    assert result["metrics"]["facet_label"]["precision"] == 17 / 18
    assert result["metrics"]["case_exact_match"]["matched_count"] == 17


def test_missing_prediction_reduces_coverage(tmp_path):
    rows = _prediction_rows(_bundle())
    rows.pop(11)
    result = _evaluate(tmp_path, rows)
    assert result["missing_prediction_case_ids"] == ["SFB-012"]
    assert result["metrics"]["prediction_coverage"] == {
        "predicted_case_count": 17,
        "total_case_count": 18,
        "rate": 17 / 18,
    }
