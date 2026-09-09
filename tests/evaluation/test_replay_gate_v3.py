import unittest

from scripts.gate_v3_intent import (
    CITY_RULE_ID,
    FINANCE_RULE_ID,
)
from scripts.replay_gate_v3 import select_v3


class ReplayGateV3Tests(unittest.TestCase):
    def setUp(self):
        self.baseline = ["a", "b", "c", "d", "e"]
        self.candidates = [*self.baseline, "target", "other"]
        self.allowed = set(self.candidates)

    def _select(self, query, rule_id=CITY_RULE_ID, *, target="target"):
        links = [{"rule_id": rule_id, "source": "a", "target": target}]
        return select_v3(
            query,
            self.baseline,
            self.candidates,
            self.allowed,
            links,
            window=20,
        )

    def test_allow_supplements_and_preserves_top_four(self):
        selected, events = self._select(
            "此次目的地是厦门，但类别还没查。出差人是部门负责人。每晚住宿费用最高多少？"
        )
        self.assertEqual(selected, ["a", "b", "c", "d", "target"])
        self.assertEqual(events[0]["action"], "supplemented")
        self.assertEqual(events[0]["gate_decision"], "ALLOW")
        self.assertFalse(events[0]["fallback_applied"])

    def test_deny_keeps_baseline_and_records_reason(self):
        selected, events = self._select(
            "目的地的类别已经核实，是二类。出差人是普通员工。请只给每晚住宿金额上限。"
        )
        self.assertEqual(selected, self.baseline)
        self.assertEqual(events[0]["action"], "denied_by_gate")
        self.assertEqual(events[0]["gate_decision"], "DENY")

    def test_unresolved_uses_explicit_fallback(self):
        selected, events = self._select("这个怎么处理？", FINANCE_RULE_ID)
        self.assertEqual(selected, ["a", "b", "c", "d", "target"])
        self.assertEqual(events[0]["gate_decision"], "UNRESOLVED")
        self.assertEqual(events[0]["action"], "supplemented")
        self.assertTrue(events[0]["fallback_applied"])

    def test_not_applicable_does_not_supplement(self):
        selected, events = self._select("一类城市包括哪些地方？请只列出制度里的城市名单。")
        self.assertEqual(selected, self.baseline)
        self.assertEqual(events[0]["action"], "not_applicable")
        self.assertEqual(events[0]["gate_decision"], "NOT_APPLICABLE")

    def test_already_present_is_recorded_without_change(self):
        selected, events = self._select("这个怎么处理？", target="b")
        self.assertEqual(selected, self.baseline)
        self.assertEqual(events[0]["action"], "already_present")

    def test_invalid_window_and_pool_are_rejected(self):
        with self.assertRaises(ValueError):
            select_v3(
                "这个怎么处理？",
                self.baseline,
                self.candidates,
                self.allowed,
                [{"rule_id": FINANCE_RULE_ID, "source": "a", "target": "target"}],
                window=10,
            )
        with self.assertRaises(ValueError):
            select_v3(
                "这个怎么处理？",
                self.baseline,
                [*self.candidates, "overflow"],
                self.allowed,
                [{"rule_id": FINANCE_RULE_ID, "source": "a", "target": "target"}],
                window=20,
            )


if __name__ == "__main__":
    unittest.main()
