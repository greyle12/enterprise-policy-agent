import json
from pathlib import Path
import tempfile
import unittest

from scripts.evaluate_gate_v3_decisions import (
    ConfirmationRequired,
    _metrics,
    _require_confirmed_review,
    _run_rows,
)
from scripts.gate_v3_intent import (
    CITY_RULE_ID,
    FINANCE_RULE_ID,
    GateDecision,
    evaluate,
)
from scripts.verify_gate_v3_decision_review import validate


ROOT = Path("artifacts/gate-v3-decision-eval-v1")


class GateV3DecisionEvaluationTests(unittest.TestCase):
    def test_pending_review_preflight_is_complete_but_not_scored(self):
        result = validate(ROOT)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["status"], "pending_user_confirmation")
        self.assertFalse(result["numeric_report_generated"])
        self.assertFalse(result["gate_executed"])
        self.assertEqual(result["case_count"], 9)
        self.assertEqual(result["parser_decisions"], {"UNRESOLVED": 9})

    def test_every_draft_case_is_currently_unresolved(self):
        draft = json.loads((ROOT / "review-draft.json").read_text(encoding="utf-8"))
        self.assertEqual(draft["status"], "pending_user_confirmation")
        for case in draft["cases"]:
            _, result = evaluate(case["query"], case["rule_id"])
            self.assertEqual(result.decision, GateDecision.UNRESOLVED, case["case_id"])

    def test_actual_decision_is_not_taken_from_expected_label(self):
        rows = [
            {
                "case_id": "synthetic-mismatch",
                "query": "这个怎么处理？",
                "rule_id": FINANCE_RULE_ID,
                "expected_decision": "ALLOW",
            }
        ]
        observed = _run_rows(rows, "test")
        self.assertEqual(observed[0]["actual_decision"], "UNRESOLVED")
        self.assertFalse(observed[0]["exact_match"])

    def test_four_value_metrics_keep_unresolved_and_not_applicable_in_denominator(self):
        rows = [
            {
                "expected_decision": "ALLOW",
                "actual_decision": "ALLOW",
                "status": "ok",
                "exact_match": True,
            },
            {
                "expected_decision": "DENY",
                "actual_decision": "UNRESOLVED",
                "status": "ok",
                "exact_match": False,
            },
            {
                "expected_decision": "UNRESOLVED",
                "actual_decision": "UNRESOLVED",
                "status": "ok",
                "exact_match": True,
            },
            {
                "expected_decision": "NOT_APPLICABLE",
                "actual_decision": "NOT_APPLICABLE",
                "status": "ok",
                "exact_match": True,
            },
            {
                "expected_decision": "ALLOW",
                "actual_decision": None,
                "status": "error",
                "exact_match": False,
            },
        ]
        metrics = _metrics(rows)
        self.assertEqual(metrics["n"], 5)
        self.assertEqual(metrics["errors"], 1)
        self.assertEqual(metrics["exact_matches"], 3)
        self.assertEqual(metrics["overall_accuracy"], 0.6)
        self.assertEqual(metrics["known_expected_n"], 4)
        self.assertEqual(metrics["known_decision_accuracy"], 0.5)
        self.assertEqual(metrics["four_value_output_coverage"], 0.8)
        self.assertEqual(metrics["known_decision_coverage"], 0.4)
        self.assertEqual(metrics["unresolved_expected_n"], 1)
        self.assertEqual(metrics["unresolved_recall"], 1.0)
        self.assertEqual(metrics["known_to_unresolved"], 1)
        self.assertEqual(metrics["unresolved_to_known"], 0)
        self.assertEqual(metrics["confusion_matrix"]["DENY"]["UNRESOLVED"], 1)
        self.assertEqual(metrics["confusion_matrix"]["ALLOW"]["ERROR"], 1)

    def test_pending_confirmation_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "freeze.json").write_text(json.dumps({}), encoding="utf-8")
            (root / "review.json").write_text(
                json.dumps({"status": "pending_user_confirmation", "cases": []}),
                encoding="utf-8",
            )
            (root / "user-confirmation.json").write_text(
                json.dumps({"status": "pending_user_confirmation"}), encoding="utf-8"
            )
            (root / "confirmed-lock.json").write_text(json.dumps({}), encoding="utf-8")
            with self.assertRaises(ConfirmationRequired):
                _require_confirmed_review(
                    root / "review.json",
                    root / "user-confirmation.json",
                    root / "confirmed-lock.json",
                )

    def test_source_parser_still_handles_confirmed_not_applicable_case(self):
        query = "一类城市包括哪些地方？请只列出制度里的城市名单。"
        _, result = evaluate(query, CITY_RULE_ID)
        self.assertEqual(result.decision, GateDecision.NOT_APPLICABLE)


if __name__ == "__main__":
    unittest.main()
