import unittest
from scripts.evaluate_gate_decisions import metrics, PAIRS, EXCLUDED


class DecisionMetricsTests(unittest.TestCase):
    def test_confusion_denominators(self):
        rows = [
            {"expected_allow": a, "v2": b}
            for a, b in [(True, True), (True, False), (False, True), (False, False)]
        ]
        m = metrics(rows, "v2")
        self.assertEqual(
            (m["tp"], m["tn"], m["fp_unnecessary_allowed"], m["fn_needed_blocked"]), (1, 1, 1, 1)
        )
        self.assertEqual(m["accuracy"], 0.5)
        self.assertIsNone(metrics([], "v2")["allow_precision"])

    def test_primary_evidence_not_mislabeled(self):
        self.assertIn("CHG-005", EXCLUDED)
        self.assertNotIn("CHG-005", PAIRS)
        self.assertEqual(len(PAIRS), 19)
