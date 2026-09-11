"""Compare semantic request Facet predictions with the confirmed sidecar offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from scripts.semantic_request_facets_adapter import (
    FACET_NAMES,
    FacetAdapterError,
    SemanticRequestFacetBundle,
    load_semantic_request_facet_bundle,
)

EVALUATION_SCHEMA_VERSION = "1.0"
PREDICTION_SCHEMA_VERSION = "1.0"
PREDICTION_KINDS = frozenset({"human_fixture", "offline_mock", "real_model"})
PREDICTED_BINDINGS = frozenset({"BOUND", "UNBOUND"})


class SemanticFacetEvaluationError(ValueError):
    """One deterministic prediction or evaluation input error."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True, slots=True)
class PredictedFacet:
    facet: str
    binding: str


@dataclass(frozen=True, slots=True)
class PredictionRecord:
    schema_version: str
    case_id: str
    predicted_facets: tuple[PredictedFacet, ...]
    line_number: int


@dataclass(frozen=True, slots=True)
class PredictionDataset:
    path: Path
    sha256: str
    records: tuple[PredictionRecord, ...]


def _strict_object(value: object, path: str, allowed_fields: Sequence[str]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SemanticFacetEvaluationError("OBJECT_TYPE", path, "value must be an object")
    allowed = set(allowed_fields)
    keys = set(value)
    unknown = keys - allowed
    if unknown:
        names = ", ".join(sorted(repr(name) for name in unknown))
        raise SemanticFacetEvaluationError("UNKNOWN_FIELD", path, f"unknown fields: {names}")
    missing = allowed - keys
    if missing:
        names = ", ".join(sorted(repr(name) for name in missing))
        raise SemanticFacetEvaluationError(
            "MISSING_FIELD", path, f"required fields missing: {names}"
        )
    return dict(value)


def _string(value: object, path: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SemanticFacetEvaluationError("STRING_TYPE", path, "value must be a string")
    if non_empty and not value:
        raise SemanticFacetEvaluationError("EMPTY_STRING", path, "value must not be empty")
    return value


def _parse_prediction_facets(value: object, path: str) -> tuple[PredictedFacet, ...]:
    if not isinstance(value, list):
        raise SemanticFacetEvaluationError("ARRAY_TYPE", path, "value must be an array")

    facets: list[PredictedFacet] = []
    seen: set[str] = set()
    for index, raw_facet in enumerate(value):
        facet_path = f"{path}[{index}]"
        item = _strict_object(raw_facet, facet_path, ("facet", "binding"))
        facet = _string(item["facet"], f"{facet_path}.facet", non_empty=True)
        if facet not in FACET_NAMES:
            raise SemanticFacetEvaluationError(
                "UNKNOWN_FACET", f"{facet_path}.facet", f"unsupported facet {facet!r}"
            )
        if facet in seen:
            raise SemanticFacetEvaluationError(
                "DUPLICATE_FACET", f"{facet_path}.facet", f"facet {facet!r} is duplicated"
            )
        binding = _string(item["binding"], f"{facet_path}.binding", non_empty=True)
        if binding not in PREDICTED_BINDINGS:
            raise SemanticFacetEvaluationError(
                "INVALID_BINDING",
                f"{facet_path}.binding",
                f"expected BOUND or UNBOUND, got {binding!r}",
            )
        seen.add(facet)
        facets.append(PredictedFacet(facet=facet, binding=binding))
    return tuple(facets)


def load_prediction_dataset(path: str | Path) -> PredictionDataset:
    """Read strict UTF-8 JSONL predictions and retain the exact file digest."""

    prediction_path = Path(path)
    try:
        raw_bytes = prediction_path.read_bytes()
    except FileNotFoundError as exc:
        raise SemanticFacetEvaluationError(
            "INPUT_READ_ERROR", "predictions", "file does not exist"
        ) from exc
    except OSError as exc:
        raise SemanticFacetEvaluationError("INPUT_READ_ERROR", "predictions", str(exc)) from exc

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SemanticFacetEvaluationError("INPUT_ENCODING_ERROR", "predictions", str(exc)) from exc

    records: list[PredictionRecord] = []
    seen_case_ids: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        record_path = f"predictions:{line_number}"
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise SemanticFacetEvaluationError("INVALID_JSON", record_path, exc.msg) from exc
        item = _strict_object(
            payload, record_path, ("schema_version", "case_id", "predicted_facets")
        )
        schema_version = _string(item["schema_version"], f"{record_path}.schema_version")
        if schema_version != PREDICTION_SCHEMA_VERSION:
            raise SemanticFacetEvaluationError(
                "SCHEMA_VERSION",
                f"{record_path}.schema_version",
                f"expected {PREDICTION_SCHEMA_VERSION}, got {schema_version}",
            )
        case_id = _string(item["case_id"], f"{record_path}.case_id", non_empty=True)
        if case_id in seen_case_ids:
            raise SemanticFacetEvaluationError(
                "DUPLICATE_CASE_ID",
                f"{record_path}.case_id",
                f"case ID {case_id!r} is duplicated",
            )
        seen_case_ids.add(case_id)
        predicted_facets = _parse_prediction_facets(
            item["predicted_facets"], f"{record_path}.predicted_facets"
        )
        records.append(
            PredictionRecord(
                schema_version=schema_version,
                case_id=case_id,
                predicted_facets=predicted_facets,
                line_number=line_number,
            )
        )

    if not records:
        raise SemanticFacetEvaluationError(
            "NO_RECORDS", "predictions", "prediction JSONL must contain at least one record"
        )
    return PredictionDataset(
        path=prediction_path,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        records=tuple(records),
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return 0.0 if precision == recall == 0.0 else None
    return 2 * precision * recall / (precision + recall)


def _expected_facets(case: Any) -> dict[str, str]:
    return {facet.facet: facet.binding for facet in case.accepted_facets}


def _predicted_facets(record: PredictionRecord | None) -> dict[str, str]:
    if record is None:
        return {}
    return {facet.facet: facet.binding for facet in record.predicted_facets}


def evaluate_predictions(
    bundle: SemanticRequestFacetBundle,
    predictions: PredictionDataset,
) -> dict[str, object]:
    """Score facet labels and binding agreement without applying a quality threshold."""

    source_case_ids = {case.case_id for case in bundle.cases}
    prediction_by_case = {record.case_id: record for record in predictions.records}
    unexpected_case_ids = sorted(set(prediction_by_case) - source_case_ids)
    if unexpected_case_ids:
        raise SemanticFacetEvaluationError(
            "UNKNOWN_CASE_ID",
            "predictions",
            f"case IDs are absent from confirmed source data: {unexpected_case_ids}",
        )

    true_positive = 0
    false_positive = 0
    false_negative = 0
    binding_match_count = 0
    binding_mismatch_count = 0
    exact_match_count = 0
    present_prediction_count = 0
    case_rows: list[dict[str, object]] = []

    for case in bundle.cases:
        prediction = prediction_by_case.get(case.case_id)
        expected = _expected_facets(case)
        predicted = _predicted_facets(prediction)
        expected_names = set(expected)
        predicted_names = set(predicted)
        matched_names = sorted(expected_names & predicted_names)
        false_positive_names = sorted(predicted_names - expected_names)
        false_negative_names = sorted(expected_names - predicted_names)
        true_positive += len(matched_names)
        false_positive += len(false_positive_names)
        false_negative += len(false_negative_names)

        binding_mismatches = [
            {
                "facet": name,
                "expected": expected[name],
                "predicted": predicted[name],
            }
            for name in matched_names
            if expected[name] != predicted[name]
        ]
        binding_mismatch_count += len(binding_mismatches)
        binding_match_count += len(matched_names) - len(binding_mismatches)

        prediction_present = prediction is not None
        present_prediction_count += prediction_present
        exact_match = prediction_present and expected == predicted
        exact_match_count += exact_match
        case_rows.append(
            {
                "case_id": case.case_id,
                "record_line": case.record_line,
                "prediction_present": prediction_present,
                "derived_expected_binding_status": case.derived_binding_status,
                "expected_facets": [
                    {"facet": name, "binding": expected[name]} for name in sorted(expected)
                ],
                "predicted_facets": [
                    {"facet": name, "binding": predicted[name]} for name in sorted(predicted)
                ],
                "true_positive_facets": matched_names,
                "false_positive_facets": false_positive_names,
                "false_negative_facets": false_negative_names,
                "binding_mismatches": binding_mismatches,
                "exact_match": exact_match,
                "excluded_unresolved_output_types": list(case.preserved_unresolved_output_types),
            }
        )

    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    return {
        "metrics": {
            "facet_label": {
                "expected_count": true_positive + false_negative,
                "predicted_count": true_positive + false_positive,
                "true_positive": true_positive,
                "false_positive": false_positive,
                "false_negative": false_negative,
                "precision": precision,
                "recall": recall,
                "f1": _f1(precision, recall),
            },
            "binding": {
                "evaluated_count": true_positive,
                "match_count": binding_match_count,
                "mismatch_count": binding_mismatch_count,
                "accuracy": _ratio(binding_match_count, true_positive),
            },
            "case_exact_match": {
                "matched_count": exact_match_count,
                "total_case_count": len(bundle.cases),
                "rate": _ratio(exact_match_count, len(bundle.cases)),
            },
            "prediction_coverage": {
                "predicted_case_count": present_prediction_count,
                "total_case_count": len(bundle.cases),
                "rate": _ratio(present_prediction_count, len(bundle.cases)),
            },
        },
        "missing_prediction_case_ids": [
            case.case_id for case in bundle.cases if case.case_id not in prediction_by_case
        ],
        "cases": case_rows,
    }


def _git_identity(root: Path) -> dict[str, object]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, encoding="utf-8", stderr=subprocess.DEVNULL
        ).strip()

    try:
        return {
            "head": git("rev-parse", "HEAD"),
            "branch": git("branch", "--show-current"),
            "working_tree_dirty": bool(git("status", "--porcelain", "--untracked-files=normal")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"head": None, "branch": None, "working_tree_dirty": None}


def _environment_evidence() -> dict[str, object]:
    versions: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            versions.setdefault(name.lower().replace("_", "-"), distribution.version)
    return {
        "python": platform.python_version(),
        "os": platform.platform(),
        "machine": platform.machine(),
        "packages": dict(sorted(versions.items())),
    }


def _base_report(
    project_root: Path,
    predictions_path: Path,
    prediction_kind: str,
    prediction_source: str,
    model_id: str,
    model_revision: str,
    prediction_sha256: str | None,
) -> dict[str, object]:
    return {
        "status": "failed",
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "evaluation_scope": "confirmed_request_facets_development_set",
        "independent_test_set": False,
        "numeric_resume_ready": False,
        "semantic_accuracy": None,
        "source": None,
        "prediction": {
            "path": str(predictions_path),
            "sha256": prediction_sha256,
            "kind": prediction_kind,
            "source": prediction_source,
            "model_id": model_id,
            "model_revision": model_revision,
            "record_count": 0,
            "case_count": 0,
        },
        "evaluator": {
            "script": "scripts/evaluate_semantic_request_facets.py",
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "git": _git_identity(project_root),
            "environment": _environment_evidence(),
            "model_inference_performed_by_evaluator": False,
            "retrieval_executed": False,
            "parser_runtime_integration": False,
        },
        "metrics": None,
        "missing_prediction_case_ids": [],
        "cases": [],
        "errors": [],
    }


def build_evaluation_report(
    project_root: Path,
    bundle: SemanticRequestFacetBundle,
    predictions: PredictionDataset,
    *,
    prediction_kind: str,
    prediction_source: str,
    model_id: str,
    model_revision: str,
) -> dict[str, object]:
    evaluated = evaluate_predictions(bundle, predictions)
    report = _base_report(
        project_root,
        predictions.path,
        prediction_kind,
        prediction_source,
        model_id,
        model_revision,
        predictions.sha256,
    )
    report["status"] = "passed"
    report["source"] = {
        "dataset_version": bundle.dataset_version,
        "source_dataset_version": bundle.source_dataset_version,
        "source_records_sha256": bundle.source_records_sha256,
        "source_proposal_sha256": bundle.source_proposal_sha256,
        "manifest_hashes_verified": bundle.manifest_hashes_verified,
        "record_count": len(bundle.records),
        "case_count": len(bundle.cases),
        "accepted_facet_instance_count": bundle.accepted_facet_instance_count,
        "allowed_facets": list(bundle.allowed_facets),
        "binding_statuses": list(bundle.binding_statuses),
    }
    report["prediction"] = {
        **report["prediction"],
        "record_count": len(predictions.records),
        "case_count": len(predictions.records),
    }
    report["metrics"] = evaluated["metrics"]
    report["missing_prediction_case_ids"] = evaluated["missing_prediction_case_ids"]
    report["cases"] = evaluated["cases"]
    report["excluded_unresolved_output_types"] = sorted(
        {
            output_type
            for case in bundle.cases
            for output_type in case.preserved_unresolved_output_types
        }
    )
    return report


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare semantic request Facet predictions with confirmed annotations offline."
    )
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--accepted", required=True, type=Path)
    parser.add_argument("--confirmation", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--prediction-kind", required=True, choices=sorted(PREDICTION_KINDS))
    parser.add_argument("--prediction-source", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def _try_file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = args.project_root.resolve()
    report = _base_report(
        project_root,
        args.predictions,
        args.prediction_kind,
        args.prediction_source,
        args.model_id,
        args.model_revision,
        _try_file_sha256(args.predictions),
    )
    exit_code = 0
    try:
        bundle = load_semantic_request_facet_bundle(
            args.records,
            args.accepted,
            args.confirmation,
            manifest_path=args.manifest,
            project_root=project_root,
        )
        predictions = load_prediction_dataset(args.predictions)
        report = build_evaluation_report(
            project_root,
            bundle,
            predictions,
            prediction_kind=args.prediction_kind,
            prediction_source=args.prediction_source,
            model_id=args.model_id,
            model_revision=args.model_revision,
        )
    except (FacetAdapterError, SemanticFacetEvaluationError) as exc:
        report["errors"] = [exc.as_dict()]
        exit_code = 1
    except Exception as exc:  # noqa: BLE001 - CLI keeps one stable failure boundary
        report["errors"] = [
            {
                "code": "INTERNAL_ERROR",
                "path": "evaluation",
                "message": f"{type(exc).__name__}: {exc}",
            }
        ]
        exit_code = 1

    if args.output is not None:
        try:
            _write_report(args.output, report)
        except OSError as exc:
            report["status"] = "failed"
            report["errors"] = [
                {
                    "code": "REPORT_WRITE_FAILED",
                    "path": str(args.output),
                    "message": str(exc),
                }
            ]
            exit_code = 1

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
