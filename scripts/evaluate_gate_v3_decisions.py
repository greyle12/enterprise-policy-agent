"""Evaluate the confirmed v3 four-valued decision contract.

The evaluator is deliberately separate from retrieval and production gates.  It
computes decisions from query text and rule IDs first, then uses confirmed
labels only to build an auditable confusion matrix.  Pending reviews fail
closed and produce no partial numeric report.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import platform
from typing import Any, Iterable

from scripts.gate_v3_intent import GateDecision, evaluate
from scripts.verify_confirmed_gate_contract import validate as validate_frozen_contract


DECISION_VALUES = tuple(decision.value for decision in GateDecision)
ERROR_VALUE = "ERROR"
MATRIX_COLUMNS = (*DECISION_VALUES, ERROR_VALUE)


class ConfirmationRequired(ValueError):
    """Raised when a review set has not been explicitly confirmed and locked."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _validate_rows(rows: Iterable[dict[str, Any]], suite: str) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{suite} row {index} is not an object")
        case_id = row.get("case_id")
        query = row.get("query")
        rule_id = row.get("rule_id")
        expected = row.get("proposed_decision", row.get("expected_decision"))
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{suite} row {index} has no case_id")
        if case_id in ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        ids.add(case_id)
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"{suite} row {case_id} has an empty query")
        if not isinstance(rule_id, str):
            raise ValueError(f"{suite} row {case_id} has no rule_id")
        if expected not in DECISION_VALUES:
            raise ValueError(f"{suite} row {case_id} has invalid expected decision: {expected}")
        validated.append(
            {
                "case_id": case_id,
                "query": query,
                "rule_id": rule_id,
                "expected_decision": expected,
            }
        )
    return validated


def _require_confirmed_review(
    review_path: Path,
    confirmation_path: Path,
    lock_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    review = _read_json(review_path)
    confirmation = _read_json(confirmation_path)
    lock = _read_json(lock_path)
    freeze_path = review_path.parent / "freeze.json"
    if not freeze_path.is_file():
        raise ConfirmationRequired(f"missing freeze file: {freeze_path}")
    freeze = _read_json(freeze_path)
    if review.get("status") != "user_confirmed":
        raise ConfirmationRequired("UNRESOLVED review is pending user confirmation")
    if confirmation.get("status") != "user_confirmed":
        raise ConfirmationRequired("UNRESOLVED confirmation is not user_confirmed")
    if any(
        isinstance(row, dict) and row.get("review_status") != "user_confirmed"
        for row in review.get("cases", [])
    ):
        raise ConfirmationRequired("one or more UNRESOLVED rows are not user_confirmed")
    expected_hashes = {
        "review_sha256": _sha256(review_path),
        "confirmation_sha256": _sha256(confirmation_path),
        "freeze_sha256": _sha256(freeze_path),
    }
    if any(lock.get(key) != value for key, value in expected_hashes.items()):
        raise ConfirmationRequired("confirmation lock does not match review, confirmation, or freeze")
    if confirmation.get("review_sha256") != expected_hashes["review_sha256"]:
        raise ConfirmationRequired("confirmation does not bind the reviewed file")
    if confirmation.get("freeze_sha256") != expected_hashes["freeze_sha256"]:
        raise ConfirmationRequired("confirmation does not bind the frozen implementation")
    return review, confirmation, freeze


def _run_rows(rows: list[dict[str, Any]], suite: str) -> list[dict[str, Any]]:
    observed: list[dict[str, Any]] = []
    for row in rows:
        base: dict[str, Any] = {
            "case_id": row["case_id"],
            "query": row["query"],
            "rule_id": row["rule_id"],
            "expected_decision": row["expected_decision"],
            "suite": suite,
        }
        try:
            intent, decision = evaluate(row["query"], row["rule_id"])
            actual = decision.decision.value
            base.update(
                {
                    "status": "ok",
                    "actual_decision": actual,
                    "exact_match": actual == row["expected_decision"],
                    "reason_code": decision.reason_code,
                    "evidence_spans": [span.as_dict() for span in decision.evidence_spans],
                    "unresolved_reasons": list(decision.unresolved_reasons),
                    "intent": intent.as_dict(),
                }
            )
        except Exception as exc:  # retain failures as evidence; never disguise them as a decision
            base.update(
                {
                    "status": "error",
                    "actual_decision": None,
                    "exact_match": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        observed.append(base)
    return observed


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected_counts = Counter(row["expected_decision"] for row in rows)
    actual_counts = Counter(
        row["actual_decision"] if row["status"] == "ok" else ERROR_VALUE for row in rows
    )
    matrix = {
        expected: {actual: 0 for actual in MATRIX_COLUMNS} for expected in DECISION_VALUES
    }
    for row in rows:
        actual = row["actual_decision"] if row["status"] == "ok" else ERROR_VALUE
        matrix[row["expected_decision"]][actual] += 1

    n = len(rows)
    expected_known = [row for row in rows if row["expected_decision"] != GateDecision.UNRESOLVED]
    actual_four_value = [row for row in rows if row["status"] == "ok"]
    actual_determinate = [
        row
        for row in rows
        if row["status"] == "ok" and row["actual_decision"] != GateDecision.UNRESOLVED
    ]
    expected_unresolved = [
        row for row in rows if row["expected_decision"] == GateDecision.UNRESOLVED
    ]
    actual_unresolved = [
        row
        for row in rows
        if row["status"] == "ok" and row["actual_decision"] == GateDecision.UNRESOLVED
    ]
    exact = sum(row["exact_match"] for row in rows)
    return {
        "n": n,
        "expected_counts": {value: expected_counts.get(value, 0) for value in DECISION_VALUES},
        "actual_counts": {
            value: actual_counts.get(value, 0) for value in (*DECISION_VALUES, ERROR_VALUE)
        },
        "confusion_matrix": matrix,
        "errors": actual_counts.get(ERROR_VALUE, 0),
        "exact_matches": exact,
        "overall_accuracy": _ratio(exact, n),
        "known_expected_n": len(expected_known),
        "known_decision_accuracy": _ratio(
            sum(row["exact_match"] for row in expected_known), len(expected_known)
        ),
        "four_value_output_coverage": _ratio(len(actual_four_value), n),
        "known_decision_coverage": _ratio(len(actual_determinate), n),
        "unresolved_expected_n": len(expected_unresolved),
        "unresolved_recall": _ratio(
            sum(row["actual_decision"] == GateDecision.UNRESOLVED for row in expected_unresolved),
            len(expected_unresolved),
        ),
        "unresolved_precision": _ratio(
            sum(row["expected_decision"] == GateDecision.UNRESOLVED for row in actual_unresolved),
            len(actual_unresolved),
        ),
        "known_to_unresolved": sum(
            row["expected_decision"] != GateDecision.UNRESOLVED
            and row["actual_decision"] == GateDecision.UNRESOLVED
            for row in rows
        ),
        "unresolved_to_known": sum(
            row["expected_decision"] == GateDecision.UNRESOLVED
            and row["status"] == "ok"
            and row["actual_decision"] != GateDecision.UNRESOLVED
            for row in rows
        ),
    }


def evaluate_confirmed(
    contract_dir: Path,
    unresolved_review: Path,
    unresolved_confirmation: Path,
    unresolved_lock: Path,
) -> dict[str, Any]:
    """Run the confirmed decision-layer evaluation and return an auditable report."""

    frozen_contract = validate_frozen_contract(contract_dir)
    if not frozen_contract.get("passed"):
        raise ConfirmationRequired("confirmed v1 contract validation failed")
    contract_path = contract_dir / "review.json"
    contract = _read_json(contract_path)
    unresolved, confirmation, freeze = _require_confirmed_review(
        unresolved_review, unresolved_confirmation, unresolved_lock
    )
    contract_rows = _validate_rows(contract.get("rows", []), "confirmed_contract_v1")
    unresolved_rows = _validate_rows(unresolved.get("cases", []), "unresolved_v1")
    if not contract_rows or not unresolved_rows:
        raise ValueError("both decision suites must contain at least one row")
    if set(row["case_id"] for row in contract_rows) & set(row["case_id"] for row in unresolved_rows):
        raise ValueError("case IDs overlap between frozen contract and UNRESOLVED set")

    suites = {
        "confirmed_contract_v1": _run_rows(contract_rows, "confirmed_contract_v1"),
        "unresolved_v1": _run_rows(unresolved_rows, "unresolved_v1"),
    }
    combined = [row for rows in suites.values() for row in rows]
    return {
        "schema_version": "1.0",
        "phase": 38,
        "step": 3,
        "substep": 2,
        "status": "completed",
        "numeric_resume_ready": True,
        "evaluation_type": "decision_layer_four_value",
        "model_inference": False,
        "retrieval_execution": False,
        "runtime_selector_changed": False,
        "label_provenance": {
            "confirmed_contract_v1": "AI-assisted source review explicitly confirmed by user; not independent double annotation",
            "unresolved_v1": confirmation.get(
                "provenance",
                "user-confirmed review; independent annotation status not asserted",
            ),
        },
        "definitions": {
            "overall_accuracy": "exact decision match over every row, including NOT_APPLICABLE and errors in the denominator",
            "known_decision_accuracy": "exact match among rows whose expected value is not UNRESOLVED",
            "four_value_output_coverage": "rows producing one of the four legal values rather than an execution error",
            "known_decision_coverage": "rows producing a determinate ALLOW, DENY, or NOT_APPLICABLE value; UNRESOLVED is excluded",
            "unresolved_recall": "expected UNRESOLVED rows that actually return UNRESOLVED",
            "unresolved_precision": "actual UNRESOLVED rows whose expected value is UNRESOLVED",
            "error_policy": "exceptions are retained as ERROR and count as incorrect; no silent fallback",
        },
        "suites": {
            name: {"metrics": _metrics(rows), "rows": rows} for name, rows in suites.items()
        },
        "combined": {"metrics": _metrics(combined), "rows": combined},
        "sources": {
            "contract_review": str(contract_path),
            "contract_sha256": _sha256(contract_path),
            "unresolved_review": str(unresolved_review),
            "unresolved_review_sha256": _sha256(unresolved_review),
            "unresolved_confirmation_sha256": _sha256(unresolved_confirmation),
            "unresolved_freeze_sha256": _sha256(unresolved_review.parent / "freeze.json"),
            "parser_sha256": _sha256(Path("scripts/gate_v3_intent.py")),
            "freeze_record": freeze,
        },
        "environment": {"python": platform.python_version()},
        "generated_at": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-dir", type=Path, required=True)
    parser.add_argument("--unresolved-review", type=Path, required=True)
    parser.add_argument("--unresolved-confirmation", type=Path, required=True)
    parser.add_argument("--unresolved-lock", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=False, exist_ok=False)
    try:
        report = evaluate_confirmed(
            args.contract_dir,
            args.unresolved_review,
            args.unresolved_confirmation,
            args.unresolved_lock,
        )
    except ConfirmationRequired as exc:
        failure = {
            "schema_version": "1.0",
            "phase": 38,
            "step": 3,
            "substep": 2,
            "status": "blocked",
            "numeric_resume_ready": False,
            "reason": str(exc),
            "model_inference": False,
            "retrieval_execution": False,
        }
        (args.output_dir / "failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:
        failure = {
            "schema_version": "1.0",
            "phase": 38,
            "step": 3,
            "substep": 2,
            "status": "blocked",
            "numeric_resume_ready": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "model_inference": False,
            "retrieval_execution": False,
        }
        (args.output_dir / "failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    report_path = args.output_dir / "four-value-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "numeric_resume_ready": report["numeric_resume_ready"],
                "combined_metrics": report["combined"]["metrics"],
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
