from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts import run_retrieval_coverage_confirmed_eval as cli


ROOT = Path(__file__).resolve().parents[2]
SOURCE_REVIEW_DIR = ROOT / "artifacts/retrieval-coverage-review-v1"


class ConfirmedCoverageEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.review_dir = Path(self.temp.name) / "review"
        shutil.copytree(SOURCE_REVIEW_DIR, self.review_dir)

    def read(self, name: str) -> dict:
        return json.loads((self.review_dir / name).read_text(encoding="utf-8"))

    def write(self, name: str, value: dict) -> None:
        (self.review_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def test_confirmed_inputs_keep_16_queries_and_preserve_cov001(self) -> None:
        inputs = cli.load_confirmed_inputs(self.review_dir)
        self.assertEqual(len(inputs.cases), 16)
        self.assertEqual(
            inputs.cases[0]["judgments"][0]["chunk_id"],
            "TRAVEL_POLICY_001__v1_0__article_007",
        )
        self.assertEqual([case["case_id"] for case in inputs.cases], list(cli.EXPECTED_CASE_IDS))
        self.assertFalse(inputs.confirmation["numeric_resume_ready"])

    def test_confirmation_is_exactly_the_15_user_accepted_cases(self) -> None:
        inputs = cli.load_confirmed_inputs(self.review_dir)
        self.assertEqual(
            tuple(inputs.confirmation["confirmed_case_ids"]),
            cli.EXPECTED_CONFIRMED_CASE_IDS,
        )
        self.assertFalse(inputs.confirmation["full_authorized_corpus_review"])
        self.assertFalse(inputs.confirmation["retrieval_executed"])

    def test_labels_are_required_and_must_be_authorized(self) -> None:
        review = self.read("review.user-confirmed.json")
        review["cases"][1]["judgments"] = []
        self.write("review.user-confirmed.json", review)
        with self.assertRaisesRegex(ValueError, "needs judgments"):
            cli.load_confirmed_inputs(self.review_dir)

        review["cases"][1]["judgments"] = [
            {
                "chunk_id": "NOT-IN-AUTHORIZED-SNAPSHOT",
                "relevance": 3,
                "rationale": "synthetic negative assertion",
            }
        ]
        self.write("review.user-confirmed.json", review)
        with self.assertRaisesRegex(ValueError, "unauthorized judgment"):
            cli.load_confirmed_inputs(self.review_dir)

    def test_query_corpus_and_selector_drift_fail_closed(self) -> None:
        review = self.read("review.user-confirmed.json")
        review["cases"][1]["query"] = "改写后的查询，不应被静默接受"
        self.write("review.user-confirmed.json", review)
        with self.assertRaisesRegex(ValueError, "query drift"):
            cli.load_confirmed_inputs(self.review_dir)

        shutil.rmtree(self.review_dir)
        shutil.copytree(SOURCE_REVIEW_DIR, self.review_dir)
        corpus = self.read("corpus.json")
        corpus["chunks"][0]["content"] += " drift"
        self.write("corpus.json", corpus)
        with self.assertRaisesRegex(ValueError, "frozen corpus manifest fingerprint mismatch"):
            cli.load_confirmed_inputs(self.review_dir)

    def test_synthetic_ids_do_not_change_public_case_ids_or_dataset_digest(self) -> None:
        inputs = cli.load_confirmed_inputs(self.review_dir)
        rows = cli.build_target_dataset_rows(inputs.cases)
        self.assertEqual(rows[0]["case_id"], "RET-901")
        self.assertEqual(rows[-1]["case_id"], "RET-916")
        self.assertEqual(rows[0]["title"], "COV-001")
        first = cli.target_dataset_bytes(inputs.cases)
        self.assertEqual(first, cli.target_dataset_bytes(inputs.cases))
        self.assertEqual(len(first.splitlines()), 16)

    def test_offline_protocol_is_explicitly_non_numeric(self) -> None:
        inputs = cli.load_confirmed_inputs(self.review_dir)
        protocol = cli._protocol(inputs, mode="offline", lock=None, dataset_sha256="a" * 64)
        self.assertFalse(protocol["numeric_resume_ready"])
        self.assertFalse(protocol["models"]["real_model_inference_required"])
        self.assertEqual(protocol["scope"], "targeted_validation_not_independent_test")

    def test_coverage_selection_is_computed_after_reranking(self) -> None:
        case = {
            "case_id": "COV-001",
            "query": "synthetic coverage query",
            "judgments": [
                {
                    "chunk_id": "target",
                    "relevance": 3,
                    "rationale": "synthetic unit-test label",
                }
            ],
        }
        report = {
            "case_results": [
                {
                    "channels": [
                        {
                            "channel": "reranked",
                            "retrieved_chunk_ids": ["anchor", "b", "c", "d", "e"],
                            "recall_at_k": {"5": 0.0},
                            "reciprocal_rank": 0.0,
                            "error": None,
                        }
                    ]
                }
            ]
        }
        rows, summary = cli.compute_coverage_rows(
            report=report,
            cases=(case,),
            candidates={
                "synthetic coverage query": {"hybrid": ["anchor", "b", "c", "d", "e", "target"]}
            },
            links=[
                {
                    "rule_id": "synthetic",
                    "source": "anchor",
                    "target": "target",
                }
            ],
            allowed={"anchor", "b", "c", "d", "e", "target"},
        )
        self.assertEqual(rows[0]["selected_ids"], ["anchor", "b", "c", "d", "target"])
        self.assertTrue(rows[0]["changed"])
        self.assertEqual(rows[0]["baseline_metrics"]["recall_at_5"], 0.0)
        self.assertEqual(rows[0]["coverage_metrics"]["recall_at_5"], 1.0)
        self.assertEqual(summary["effective_n"], 1)
        self.assertEqual(summary["recall_at_5"], 1.0)

    def test_existing_output_directory_is_never_overwritten(self) -> None:
        output = Path(self.temp.name) / "existing"
        output.mkdir()
        sentinel = output / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = cli.main(["--mode", "offline", "--output-dir", str(output)])
        self.assertEqual(code, 2)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertFalse((output / "failure.json").exists())


if __name__ == "__main__":
    unittest.main()
