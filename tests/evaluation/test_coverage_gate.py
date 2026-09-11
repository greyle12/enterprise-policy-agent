import unittest
from scripts.experiment_coverage_gate import gate, select_gated


class GateTests(unittest.TestCase):
    def test_control_veto(self):
        for variant in ("explicit_veto", "positive_intent"):
            self.assertFalse(gate("是否强制合住", "city_category_for_lodging_table", variant)[0])
            self.assertFalse(
                gate("不需要审批流程", "finance_review_for_overdue_expense", variant)[0]
            )

    def test_implicit_intent_tradeoff(self):
        rule = "finance_review_for_overdue_expense"
        self.assertTrue(gate("这种超时怎么处理", rule, "explicit_veto")[0])
        self.assertFalse(gate("这种超时怎么处理", rule, "positive_intent")[0])
        self.assertTrue(gate("需要哪些审批", rule, "positive_intent")[0])

    def test_veto_restores_full_baseline(self):
        base = ["a", "b", "c", "d", "e"]
        proposal = ["a", "b", "c", "d", "f"]
        events = [{"action": "supplemented", "rule_id": "city_category_for_lodging_table"}]
        self.assertEqual(select_gated("合住", base, proposal, events, "explicit_veto")[0], base)
        self.assertEqual(
            select_gated("住宿限额", base, proposal, events, "positive_intent")[0], proposal
        )
        self.assertEqual(select_gated("合住", base, base, [], "explicit_veto")[0], base)
        with self.assertRaises(ValueError):
            gate("住宿", "unknown", "explicit_veto")
