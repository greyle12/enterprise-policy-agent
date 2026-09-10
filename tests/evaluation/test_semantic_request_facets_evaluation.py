from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.evaluate_semantic_request_facets import (
    SemanticFacetEvaluationError,
    build_evaluation_report,
    load_prediction_dataset,
    evaluate_predictions,
)
from scripts.semantic_request_facets_adapter import load_semantic_request_facet_bundle

ROOT = Path(__file__).resolve().parents[2]
RECORDS_PATH = ROOT / "docs/gate_v3/semantic-dev-v1-confirmed/records.jsonl"
ACCEPTED_PATH = ROOT / "docs/gate_v3/semantic-request-facets-v1-confirmed/accepted.json"
CONFIRMATION_PATH = ROOT / "docs/gate_v3/semantic-request-facets-v1-confirmed/confirmation.json"
MANIFEST_PATH = ROOT / "docs/gate_v3/semantic-request-facets-v1-confirmed/manifest.json"
_CASE_FACETS = {
    "COV-007": [("MATERIAL", "BOUND"), ("RESPONSIBLE_ROLE", "BOUND")],
    "COV-008": [("DEADLINE", "BOUND")],
    "COV-009": [("PROCEDURE", "BOUND")],
    "COV-010": [("DEADLINE", "BOUND"), ("RESPONSIBLE_ROLE", "BOUND")],
    "V3U-004": [("PROCEDURE", "UNBOUND")],
    "V3U-006": [],
    "V3U-009": [("PROCESS_CHOICE", "UNBOUND")],
}


def _bundle():
    return load_semantic_request_facet_bundle(
        RECORDS_PATH,
        ACCEPTED_PATH,
        CONFIRMATION_PATH,
        manifest_path=MANIFEST_PATH,
        project_root=ROOT,
    )


def _row(case_id: str, facets: list[tuple[str, str]] | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "predicted_facets": [
            {"facet": facet, "binding": binding}
            for facet, binding in (_CASE_FACETS[case_id] if facets is None else facets)
        ],
    }


def _prediction_text(rows: list[dict[str, object]]) -> str:
    return "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n"


def _write_predictions(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text(_prediction_text(rows), encoding="utf-8")
    return path


def _load_predictions(path: Path):
    return load_prediction_dataset(path)


def _evaluation_cli_command(predictions_path: Path, report_path: Path) -> list[str]:
    return [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "scripts.evaluate_semantic_request_facets",
        "--records",
        str(RECORDS_PATH),
        "--accepted",
        str(ACCEPTED_PATH),
        "--confirmation",
        str(CONFIRMATION_PATH),
        "--manifest",
        str(MANIFEST_PATH),
        "--predictions",
        str(predictions_path),
        "--project-root",
        str(ROOT),
        "--prediction-kind",
        "human_fixture",
        "--prediction-source",
        "unit-test-fixture",
        "--model-id",
        "none",
        "--model-revision",
        "none",
        "--output",
        str(report_path),
    ]


def _run_evaluation_cli(predictions_path: Path, report_path: Path):
    result = subprocess.run(
        _evaluation_cli_command(predictions_path, report_path),
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    stdout_report = json.loads(result.stdout)
    file_report = json.loads(report_path.read_text(encoding="utf-8"))
    return result, stdout_report, file_report


def test_perfect_predictions_report_provenance_and_facet_metrics(tmp_path: Path) -> None:
    predictions_path = _write_predictions(
        tmp_path / "predictions.jsonl",
        [_row(case_id) for case_id in _CASE_FACETS],
    )
    report = build_evaluation_report(
        ROOT,
        _bundle(),
        _load_predictions(predictions_path),
        prediction_kind="human_fixture",
        prediction_source="unit-test-fixture",
        model_id="none",
        model_revision="none",
    )

    assert report["status"] == "passed"
    assert report["semantic_accuracy"] is None
    assert report["independent_test_set"] is False
    assert report["numeric_resume_ready"] is False
    assert report["source"]["dataset_version"] == "semantic-request-facets-v0.1-confirmed-1"
    assert report["source"]["source_dataset_version"] == "semantic-dev-v1-confirmed-1"
    assert report["source"]["accepted_facet_instance_count"] == 8
    assert re.fullmatch(r"[0-9a-f]{64}", report["prediction"]["sha256"])
    assert report["prediction"]["kind"] == "human_fixture"
    assert report["prediction"]["model_id"] == "none"
    assert re.fullmatch(r"[0-9a-f]{40}", report["evaluator"]["git"]["head"])
    assert report["evaluator"]["model_inference_performed_by_evaluator"] is False

    label_metrics = report["metrics"]["facet_label"]
    assert label_metrics == {
        "expected_count": 8,
        "predicted_count": 8,
        "true_positive": 8,
        "false_positive": 0,
        "false_negative": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert report["metrics"]["binding"]["accuracy"] == 1.0
    assert report["metrics"]["case_exact_match"]["matched_count"] == 7
    assert report["metrics"]["prediction_coverage"]["rate"] == 1.0
    assert report["missing_prediction_case_ids"] == []
    assert "request_act" in report["excluded_unresolved_output_types"]


def test_wrong_binding_and_missing_prediction_remain_visible(tmp_path: Path) -> None:
    rows = [_row(case_id) for case_id in _CASE_FACETS if case_id != "V3U-006"]
    rows[0] = _row("COV-007", [("MATERIAL", "BOUND"), ("PROCEDURE", "BOUND")])
    rows[4] = _row("V3U-004", [("PROCEDURE", "BOUND")])
    predictions = _load_predictions(_write_predictions(tmp_path / "predictions.jsonl", rows))

    evaluated = evaluate_predictions(_bundle(), predictions)

    label_metrics = evaluated["metrics"]["facet_label"]
    assert label_metrics["true_positive"] == 7
    assert label_metrics["false_positive"] == 1
    assert label_metrics["false_negative"] == 1
    assert evaluated["metrics"]["binding"]["mismatch_count"] == 1
    assert evaluated["metrics"]["prediction_coverage"]["predicted_case_count"] == 6
    assert evaluated["missing_prediction_case_ids"] == ["V3U-006"]
    rows_by_id = {row["case_id"]: row for row in evaluated["cases"]}
    assert rows_by_id["COV-007"]["false_positive_facets"] == ["PROCEDURE"]
    assert rows_by_id["V3U-004"]["binding_mismatches"] == [
        {"facet": "PROCEDURE", "expected": "UNBOUND", "predicted": "BOUND"}
    ]
    assert rows_by_id["V3U-006"]["prediction_present"] is False
    assert rows_by_id["V3U-006"]["exact_match"] is False


@pytest.mark.parametrize(
    ("row", "error_code"),
    [
        (_row("COV-007", [("UNKNOWN", "BOUND")]), "UNKNOWN_FACET"),
        (_row("COV-007", [("MATERIAL", "BOUND"), ("MATERIAL", "BOUND")]), "DUPLICATE_FACET"),
    ],
)
def test_prediction_structure_errors_are_rejected(
    tmp_path: Path,
    row: dict[str, object],
    error_code: str,
) -> None:
    with pytest.raises(SemanticFacetEvaluationError, match=error_code):
        load_prediction_dataset(_write_predictions(tmp_path / "predictions.jsonl", [row]))


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        pytest.param("not-json\n", "INVALID_JSON", id="invalid-json"),
        pytest.param(
            json.dumps(_row("COV-007", [("MATERIAL", "INVALID")])),
            "INVALID_BINDING",
            id="invalid-binding",
        ),
        pytest.param(
            json.dumps({**_row("COV-007"), "extra": True}),
            "UNKNOWN_FIELD",
            id="unknown-field",
        ),
        pytest.param(
            _prediction_text([_row("COV-007"), _row("COV-007")]),
            "DUPLICATE_CASE_ID",
            id="duplicate-case-id",
        ),
        pytest.param("\n  \n", "NO_RECORDS", id="empty-file"),
    ],
)
def test_prediction_input_contract_errors_are_rejected(
    tmp_path: Path,
    payload: str,
    error_code: str,
) -> None:
    path = tmp_path / "predictions.jsonl"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(SemanticFacetEvaluationError, match=error_code):
        load_prediction_dataset(path)


def test_all_empty_predictions_use_explicit_zero_denominator_metrics(tmp_path: Path) -> None:
    predictions = _load_predictions(
        _write_predictions(
            tmp_path / "predictions.jsonl",
            [_row(case_id, []) for case_id in _CASE_FACETS],
        )
    )

    evaluated = evaluate_predictions(_bundle(), predictions)

    assert evaluated["metrics"]["facet_label"] == {
        "expected_count": 8,
        "predicted_count": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 8,
        "precision": None,
        "recall": 0.0,
        "f1": None,
    }
    assert evaluated["metrics"]["binding"] == {
        "evaluated_count": 0,
        "match_count": 0,
        "mismatch_count": 0,
        "accuracy": None,
    }
    assert evaluated["metrics"]["case_exact_match"] == {
        "matched_count": 1,
        "total_case_count": 7,
        "rate": 1 / 7,
    }
    assert evaluated["metrics"]["prediction_coverage"]["rate"] == 1.0


def test_unknown_case_id_is_rejected_after_prediction_decode(tmp_path: Path) -> None:
    predictions = _load_predictions(
        _write_predictions(
            tmp_path / "predictions.jsonl",
            [
                _row("COV-007"),
                {"schema_version": "1.0", "case_id": "UNKNOWN", "predicted_facets": []},
            ],
        )
    )

    with pytest.raises(SemanticFacetEvaluationError, match="UNKNOWN_CASE_ID"):
        evaluate_predictions(_bundle(), predictions)


def test_cli_writes_full_success_report(tmp_path: Path) -> None:
    predictions_path = _write_predictions(
        tmp_path / "predictions.jsonl",
        [_row(case_id) for case_id in _CASE_FACETS],
    )
    report_path = tmp_path / "reports" / "facet-evaluation.json"
    result, stdout_report, file_report = _run_evaluation_cli(predictions_path, report_path)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert stdout_report == file_report
    assert stdout_report["status"] == "passed"
    assert stdout_report["metrics"]["facet_label"]["f1"] == 1.0


def test_cli_failure_writes_structured_report_to_stdout_and_file(tmp_path: Path) -> None:
    predictions_path = _write_predictions(
        tmp_path / "predictions.jsonl",
        [_row("COV-007", [("MATERIAL", "INVALID")])],
    )
    report_path = tmp_path / "reports" / "facet-evaluation.json"

    result, stdout_report, file_report = _run_evaluation_cli(predictions_path, report_path)

    assert result.returncode == 1
    assert result.stderr == ""
    assert stdout_report == file_report
    assert stdout_report["status"] == "failed"
    assert stdout_report["metrics"] is None
    assert stdout_report["errors"] == [
        {
            "code": "INVALID_BINDING",
            "path": "predictions:1.predicted_facets[0].binding",
            "message": "expected BOUND or UNBOUND, got 'INVALID'",
        }
    ]
    assert re.fullmatch(r"[0-9a-f]{64}", stdout_report["prediction"]["sha256"])


def test_cli_keeps_valid_low_score_and_missing_case_as_success(tmp_path: Path) -> None:
    predictions_path = _write_predictions(
        tmp_path / "predictions.jsonl",
        [_row(case_id, []) for case_id in _CASE_FACETS if case_id != "V3U-009"],
    )
    report_path = tmp_path / "reports" / "facet-evaluation.json"

    result, stdout_report, file_report = _run_evaluation_cli(predictions_path, report_path)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert stdout_report == file_report
    assert stdout_report["status"] == "passed"
    assert stdout_report["errors"] == []
    assert stdout_report["missing_prediction_case_ids"] == ["V3U-009"]
    assert stdout_report["metrics"]["facet_label"]["f1"] is None
    assert stdout_report["metrics"]["prediction_coverage"] == {
        "predicted_case_count": 6,
        "total_case_count": 7,
        "rate": 6 / 7,
    }
    missing_row = next(row for row in stdout_report["cases"] if row["case_id"] == "V3U-009")
    assert missing_row["prediction_present"] is False
    assert missing_row["false_negative_facets"] == ["PROCESS_CHOICE"]
