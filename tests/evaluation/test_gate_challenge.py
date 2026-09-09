import unittest
from scripts.run_gate_challenge import load_challenge, score_selection


class ChallengeTests(unittest.TestCase):
    def test_confirmed_frozen_inputs(self):
        draft, confirmation = load_challenge()
        self.assertEqual(len(draft["cases"]), 12)
        self.assertEqual(confirmation["status"], "user_confirmed")

    def test_no_opportunity_is_not_success(self):
        case = {
            "case_id": "fixture",
            "query": "不需要审批流程",
            "supplement_needed": False,
            "judgments": [{"chunk_id": "a", "grade": 3}],
        }
        row = score_selection(case, list("abcde"), list("abcdef"), set("abcdef"), [], 20)
        self.assertFalse(row["opportunity"])
        self.assertFalse(row["blocked"])

    def test_harmful_veto_is_reported(self):
        case = {
            "case_id": "fixture",
            "query": "不是不需要审批流程",
            "supplement_needed": True,
            "judgments": [{"chunk_id": "f", "grade": 3}],
        }
        links = [{"source": "a", "target": "f", "rule_id": "finance_review_for_overdue_expense"}]
        row = score_selection(case, list("abcde"), list("abcdef"), set("abcdef"), links, 40)
        self.assertTrue(row["blocked"])
        self.assertIn("recall_at_5", row["regressions"])
