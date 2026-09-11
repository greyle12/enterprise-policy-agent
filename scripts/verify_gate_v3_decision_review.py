"""Validate the pending v3 four-value review set without scoring it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.gate_v3_intent import GateDecision, evaluate, relation_for
from scripts.verify_confirmed_gate_contract import validate as validate_frozen_contract


DEFAULT_ROOT = Path("artifacts/gate-v3-decision-eval-v1")
VALID_DECISIONS = {decision.value for decision in GateDecision}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def validate(root: Path = DEFAULT_ROOT) -> dict[str, object]:
    """Return a machine-readable preflight result; never runs an evaluation report."""

    checks: dict[str, bool] = {}
    errors: list[str] = []
    draft_path = root / "review-draft.json"
    freeze_path = root / "freeze.json"
    lock_path = root / "draft-lock.json"

    try:
        draft = _read_json(draft_path)
        freeze = _read_json(freeze_path)
        draft_lock = _read_json(lock_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "status": "blocked",
            "checks": {},
            "errors": [str(exc)],
            "numeric_report_generated": False,
            "gate_executed": False,
            "inference_executed": False,
        }

    checks["draft_pending"] = draft.get("status") == "pending_user_confirmation"
    label_policy = draft.get("label_policy", {})
    checks["confirmation_required"] = bool(
        isinstance(label_policy, dict)
        and label_policy.get("confirmation_required_before_scoring") is True
    )
    checks["freeze_pending"] = freeze.get("status") == "pending_user_confirmation"
    checks["draft_lock_matches"] = (
        draft_lock.get("draft_sha256") == _sha256(draft_path)
        and draft_lock.get("freeze_sha256") == _sha256(freeze_path)
    )
    checks["no_evaluation_executed"] = (
        draft_lock.get("gate_executed") is False
        and draft_lock.get("inference_executed") is False
    )

    raw_cases = draft.get("cases")
    cases = raw_cases if isinstance(raw_cases, list) else []
    checks["non_empty_cases"] = bool(cases)
    case_ids: list[str] = []
    parser_decisions: dict[str, int] = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"case {index} is not an object")
            continue
        case_id = case.get("case_id")
        if isinstance(case_id, str):
            case_ids.append(case_id)
        else:
            errors.append(f"case {index} has no string case_id")
        rule_id = case.get("rule_id")
        query = case.get("query")
        if not isinstance(rule_id, str) or not isinstance(query, str) or not query.strip():
            errors.append(f"case {case_id!r} has invalid rule_id/query")
            continue
        try:
            relation_for(rule_id)
            _, result = evaluate(query, rule_id)
        except (TypeError, ValueError) as exc:
            errors.append(f"case {case_id!r} cannot be parsed: {exc}")
            continue
        actual = result.decision.value
        parser_decisions[actual] = parser_decisions.get(actual, 0) + 1
        if case.get("proposed_decision") != GateDecision.UNRESOLVED.value:
            errors.append(f"case {case_id!r} proposed decision is not UNRESOLVED")
        if case.get("review_status") != "pending_user_confirmation":
            errors.append(f"case {case_id!r} is not pending confirmation")
        if not isinstance(case.get("target_evidence"), list) or not case["target_evidence"]:
            errors.append(f"case {case_id!r} has no source-check target evidence")
        if actual != GateDecision.UNRESOLVED.value:
            errors.append(
                f"case {case_id!r} preflight decision is {actual}, expected UNRESOLVED"
            )

    checks["unique_case_ids"] = len(case_ids) == len(set(case_ids)) == len(cases)
    checks["all_proposed_unresolved"] = not any(
        isinstance(case, dict) and case.get("proposed_decision") != GateDecision.UNRESOLVED.value
        for case in cases
    )
    checks["parser_preflight_unresolved"] = (
        len(cases) > 0
        and parser_decisions.get(GateDecision.UNRESOLVED.value, 0) == len(cases)
        and sum(parser_decisions.values()) == len(cases)
    )

    frozen_files = freeze.get("frozen_files", {})
    if not isinstance(frozen_files, dict) or not frozen_files:
        checks["frozen_inputs_unchanged"] = False
        errors.append("freeze.json has no frozen_files mapping")
    else:
        frozen_ok = True
        for filename, expected in frozen_files.items():
            path = Path(filename)
            try:
                if not isinstance(expected, str) or _sha256(path) != expected:
                    frozen_ok = False
                    errors.append(f"frozen input changed: {filename}")
            except OSError as exc:
                frozen_ok = False
                errors.append(f"cannot read frozen input {filename}: {exc}")
        checks["frozen_inputs_unchanged"] = frozen_ok

    frozen_contract = validate_frozen_contract()
    checks["confirmed_v1_contract"] = bool(frozen_contract.get("passed"))
    if not checks["confirmed_v1_contract"]:
        errors.append("confirmed v1 contract validation failed")

    passed = all(checks.values()) and not errors
    return {
        "passed": passed,
        "status": "pending_user_confirmation" if passed else "blocked",
        "checks": checks,
        "errors": errors,
        "case_count": len(cases),
        "parser_decisions": parser_decisions,
        "numeric_report_generated": False,
        "gate_executed": False,
        "inference_executed": False,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    result = validate(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
