from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from scripts import manage_retrieval_coverage_review as manager


class CoverageReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "review"
        chunk = {
            "chunk_id": "synthetic-authorized",
            "document_title": "Test policy",
            "document_version": "1",
            "content": "Synthetic source, not a real model result",
        }
        corpus = {
            "chunks": [chunk],
            "allowed_chunk_ids": [chunk["chunk_id"]],
            "full_chunk_manifest_sha256": manager.canonical_digest([chunk]),
        }
        evidence = {
            "status": "completed",
            "mode": "bge",
            "real_model_inference": True,
            "corpus_sha256": corpus["full_chunk_manifest_sha256"],
            "git": {},
            "model_lock": {},
            "configuration": {},
            "cohorts": [{"name": "old"}],
        }
        archive = self.root / "synthetic.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("evidence.json", json.dumps(evidence))
            z.writestr("corpus.json", json.dumps(corpus))
            z.writestr("old\\dataset.jsonl", json.dumps({"query": "old synthetic query"}))
        self.result = manager.prepare(archive, self.output)

    def read(self, name):
        return json.loads((self.output / name).read_text(encoding="utf-8"))

    def write(self, name, value):
        (self.output / name).write_text(json.dumps(value), encoding="utf-8")

    def approve_synthetic_fixture(self):
        review = self.read("review.json")
        for case in review["cases"]:
            case["answerability"] = "answerable"
            case["judgments"] = [
                {
                    "chunk_id": "synthetic-authorized",
                    "relevance": 3,
                    "rationale": "Synthetic test assertion, not actual human review",
                }
            ]
            case["review"].update(
                status="approved",
                reviewer="SYNTHETIC-UNIT-TEST",
                reviewed_at=datetime.now(UTC).isoformat(),
                checked_all_authorized_chunks=True,
            )
        self.write("review.json", review)

    def test_prepare_leaves_all_labels_empty_and_no_scores(self):
        self.assertEqual(self.result["status"], "awaiting_human_review")
        self.assertFalse(self.result["retrieval_executed"])
        self.assertTrue(all(not c["judgments"] for c in self.read("review.json")["cases"]))
        self.assertNotIn("retrieved_chunk_ids", (self.output / "corpus-review.md").read_text())

    def test_all_pending_queries_remain_in_denominator(self):
        report = manager.validate(self.output)
        self.assertFalse(report["passed"])
        self.assertEqual(report["case_count"], report["blocked_case_count"])

    def test_complete_synthetic_declarations_only_release_review_not_metrics(self):
        self.approve_synthetic_fixture()
        report = manager.validate(self.output)
        self.assertTrue(report["passed"])
        self.assertFalse(report["numeric_resume_ready"])
        self.assertFalse(report["retrieval_executed"])

    def test_query_rule_corpus_and_selector_drift_are_rejected(self):
        for name in ("queries.json", "rules.json", "corpus.json"):
            original = self.read(name)
            self.write(name, {**original, "tampered": True})
            with self.assertRaises(ValueError):
                manager.validate(self.output)
            self.write(name, original)
        alternate = self.root / "alternate.py"
        alternate.write_text("changed selector")
        with patch.object(manager, "SELECTOR", alternate):
            with self.assertRaisesRegex(ValueError, "Selector changed"):
                manager.validate(self.output)

    def test_missing_or_rewritten_case_cannot_be_silently_excluded(self):
        original = self.read("review.json")
        review = self.read("review.json")
        review["cases"].pop()
        self.write("review.json", review)
        with self.assertRaises(ValueError):
            manager.validate(self.output)
        original["cases"][0]["query"] = "changed query"
        self.write("review.json", original)
        with self.assertRaises(ValueError):
            manager.validate(self.output)

    def test_invalid_labels_or_seen_rankings_block_release(self):
        self.approve_synthetic_fixture()
        original = self.read("review.json")
        changes = [
            lambda c: c["judgments"][0].update(chunk_id="unauthorized"),
            lambda c: c["judgments"].append(dict(c["judgments"][0])),
            lambda c: c["judgments"][0].update(relevance=True),
            lambda c: c["judgments"][0].update(rationale=""),
            lambda c: c["review"].update(viewed_retrieval_outputs=True),
            lambda c: c["review"].update(reviewed_at="2000-01-01T00:00:00+00:00"),
            lambda c: c.update(answerability="unanswerable"),
        ]
        for change in changes:
            review = json.loads(json.dumps(original))
            change(review["cases"][0])
            self.write("review.json", review)
            report = manager.validate(self.output)
            self.assertFalse(report["passed"])
            self.assertEqual(report["blocked_case_count"], 1)

    def test_windows_line_endings_do_not_change_selector_identity(self):
        alternate = self.root / "windows.py"
        alternate.write_bytes(
            manager.SELECTOR.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        )
        with patch.object(manager, "SELECTOR", alternate):
            self.assertEqual(
                manager.validate(self.output)["blocked_case_count"], self.result["case_count"]
            )

    def test_report_writer_refuses_to_overwrite_review(self):
        original = (self.output / "review.json").read_bytes()
        with self.assertRaises(FileExistsError):
            manager.write_json(self.output / "review.json", {"wrong": True})
        self.assertEqual((self.output / "review.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
