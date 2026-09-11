import unittest
from scripts.run_gate_v2_unseen import load_challenge, score_selection


class UnseenRunnerTests(unittest.TestCase):
    def test_confirmation(self):
        draft, confirmation = load_challenge()
        self.assertEqual(len(draft["cases"]), 8)
        self.assertEqual(confirmation["status"], "user_confirmed")

    def test_four_variants_and_opportunity(self):
        case = {
            "case_id": "synthetic",
            "query": "普通住宿限额",
            "supplement_needed": True,
            "judgments": [{"chunk_id": "f", "grade": 3}],
        }
        links = [{"source": "a", "target": "f", "rule_id": "city_category_for_lodging_table"}]
        r = score_selection(case, list("abcde"), list("abcdef"), set("abcdef"), links, 20)
        self.assertEqual(set(r["ids"]), {"raw", "frozen", "v1", "v2"})
        self.assertTrue(r["opportunity"])
        self.assertEqual(r["ids"]["v2"], list("abcdf"))
        self.assertEqual(r["metrics"]["v2"]["recall_at_5"], 1)
        self.assertEqual(r["regressions"], [])

    def test_no_proposal_not_counted_as_block(self):
        case = {
            "case_id": "synthetic",
            "query": "住宿",
            "supplement_needed": False,
            "judgments": [{"chunk_id": "a", "grade": 3}],
        }
        r = score_selection(case, list("abcde"), list("abcde"), set("abcde"), [], 40)
        self.assertFalse(r["opportunity"])
        self.assertFalse(r["blocked"])
        self.assertFalse(r["v1_blocked"])
