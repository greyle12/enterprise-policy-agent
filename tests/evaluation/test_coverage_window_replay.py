import unittest

from scripts.replay_coverage_windows import select_window
from scripts.run_retrieval_coverage_ablation import select_coverage


class WindowReplayTests(unittest.TestCase):
    def test_explicit_window_and_preservation(self):
        pool = [str(i) for i in range(40)]
        links = [{"source": "0", "target": "30", "rule_id": "test"}]
        with self.assertRaises(ValueError):
            select_window(pool[:5], pool, set(pool), links, window=20)
        selected, events = select_window(pool[:5], pool, set(pool), links, window=40)
        self.assertEqual(selected, pool[:4] + ["30"])
        self.assertEqual(events[0]["evicted"], ["4"])

    def test_parity_and_invalid_inputs(self):
        pool = [str(i) for i in range(20)]
        links = [{"source": "0", "target": "19", "rule_id": "test"}]
        self.assertEqual(
            select_window(pool[:5], pool, set(pool), links, window=20),
            select_coverage(pool[:5], pool, set(pool), links),
        )
        for window, candidates, allowed in [
            (21, pool, set(pool)),
            (40, pool + ["0"], set(pool)),
            (40, pool, set(pool[1:])),
        ]:
            with self.assertRaises(ValueError):
                select_window(pool[:5], candidates, allowed, links, window=window)


if __name__ == "__main__":
    unittest.main()
