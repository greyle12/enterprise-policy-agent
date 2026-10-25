"""Integration contracts for evidence-bound comparisons and human review intake."""

import hashlib
import json
from pathlib import Path

import pytest

from app.evaluation.failure_pipeline import failure_cards
from app.evaluation.pairwise import execution_eligible
from app.evaluation.workflow_evaluation import load_workflow_cases, run_sqlite_cases
from scripts.review_workflow_regressions import promote

ROOT = Path(__file__).resolve().parents[2]


def test_pairwise_rejects_declared_pass_and_forged_assertion():
    candidate = {"deterministic": {"safety_passed": True, "final_state_passed": True}}
    assert not execution_eligible(candidate, "WF-001")
    candidate["workflow_result"] = {
        "case_id": "WF-001",
        "assertions": [
            {"name": "turn_0." + name, "expected": value, "actual": value, "passed": True}
            for name, value in (
                ("error", None),
                ("side_effect_count", 0),
                ("duplicate_side_effect", True),
                ("persisted_phase", "idle"),
            )
        ],
    }
    assert execution_eligible(candidate, "WF-001")
    candidate["workflow_result"]["assertions"][2]["actual"] = False
    assert not execution_eligible(candidate, "WF-001")
    assert not execution_eligible(candidate, "WF-002")


def test_review_promotes_only_bound_explicitly_reviewed_case(tmp_path):
    dataset = ROOT / "tests/evaluation/workflow_cases.jsonl"
    case = load_workflow_cases(dataset)[0]
    result = {
        "case_id": case.case_id,
        "split": case.split,
        "passed": False,
        "assertions": [
            {"name": "turn_0.tool_arguments", "passed": False, "expected": True, "actual": False}
        ],
    }
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "manifest": {"dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest()},
                "results": [result],
            }
        ),
        encoding="utf-8",
    )
    card = failure_cards([result])[0]
    assert card["failure_categories"] == ["tool_argument"]
    decisions = tmp_path / "decisions.jsonl"
    review = {
        "case_id": case.case_id,
        "source_sha256": card["source_sha256"],
        "reviewer": "fixture-reviewer",
        "decision": "approve",
        "rationale": "synthetic review contract test only",
    }
    decisions.write_text(json.dumps(review), encoding="utf-8")
    output = tmp_path / "regression.jsonl"
    assert promote(dataset, report, decisions, output) == 1
    promoted = load_workflow_cases(output)[0]
    assert promoted.turns == case.turns and promoted.split == "regression"
    with pytest.raises(FileExistsError):
        promote(dataset, report, decisions, output)
    review["source_sha256"] = "wrong"
    decisions.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="execution fingerprint"):
        promote(dataset, report, decisions, tmp_path / "other.jsonl")


@pytest.mark.parametrize("case_id,corrupt", [("WF-013", "authorization"), ("WF-017", "recovery")])
async def test_security_recovery_assertions_reject_wrong_expectation(tmp_path, case_id, corrupt):
    cases = load_workflow_cases(ROOT / "tests/evaluation/workflow_safety_cases.jsonl")
    case = next(c for c in cases if c.case_id == case_id)
    turn = case.turns[0].model_copy(
        update={"authorization_violation": True}
        if corrupt == "authorization"
        else {"recovered": False}
    )
    mutated = case.model_copy(update={"turns": (turn,)})
    result = (await run_sqlite_cases([mutated], tmp_path, ROOT / "data/policies"))[0]
    assert not result["passed"]
    assert any(not a["passed"] for a in result["assertions"])
