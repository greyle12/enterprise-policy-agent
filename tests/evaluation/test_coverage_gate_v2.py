import unittest
from scripts.coverage_gate_v2 import decide, select_v2


class ClauseGateTests(unittest.TestCase):
    def test_local_exclusion_does_not_cancel_other_demand(self):
        for q in ["不问同住安排，只查每晚住宿限额", "每晚住宿上限是多少？必须合住吗？"]:
            self.assertTrue(decide(q, "city_category_for_lodging_table")[0])

    def test_double_negative_and_explicit_scope(self):
        rule = "finance_review_for_overdue_expense"
        self.assertTrue(decide("不是不需要审批流程：财务要做什么", rule)[0])
        self.assertFalse(decide("不要展开审批流程，只查拒收时限", rule)[0])
        self.assertFalse(decide("只问迟交原因，其他环节先不讲", rule)[0])

    def test_question_not_known_category(self):
        rule = "city_category_for_lodging_table"
        self.assertTrue(decide("算不算一类城市？每晚多少钱", rule)[0])
        self.assertFalse(decide("已确认三类城市每晚限额", rule)[0])

    def test_selection_boundary(self):
        base = list("abcde")
        frozen = list("abcdf")
        ev = [{"action": "supplemented", "rule_id": "city_category_for_lodging_table"}]
        self.assertEqual(select_v2("强制合住吗", base, frozen, ev)[0], base)
        self.assertEqual(select_v2("每晚多少钱", base, frozen, ev)[0], frozen)
        self.assertEqual(select_v2("合住", base, base, []), (base, []))
