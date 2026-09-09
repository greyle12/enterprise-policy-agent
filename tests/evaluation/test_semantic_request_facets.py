from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.semantic_parse_contract import SemanticParseRecord, validate_record


ROOT = Path(__file__).resolve().parents[2]
FACETS_PATH = ROOT / "docs" / "gate_v3" / "semantic-request-facets-v1" / "facets.json"
RECORDS_PATH = ROOT / "docs" / "gate_v3" / "semantic-dev-v1-confirmed" / "records.jsonl"


class SemanticRequestFacetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.facets = json.loads(FACETS_PATH.read_text(encoding="utf-8"))
        cls.records = [
            json.loads(line)
            for line in RECORDS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cls.by_case = {case["case_id"]: case for case in cls.facets["cases"]}

    def test_proposal_status_and_vocabulary_are_explicit(self) -> None:
        self.assertEqual(
            self.facets["facet_version"],
            "semantic-request-facets-v0.1-draft-1",
        )
        self.assertEqual(self.facets["status"], "proposal_pending_confirmation")
        self.assertEqual(self.facets["sample_count"], 7)
        self.assertEqual(self.facets["confirmed_facet_count"], 0)
        self.assertFalse(self.facets["independent_test_set"])
        self.assertFalse(self.facets["numeric_resume_ready"])
        self.assertFalse(self.facets["model_inference"])
        self.assertFalse(self.facets["retrieval_executed"])
        self.assertEqual(
            {facet["facet"] for facet in self.facets["allowed_facets"]},
            {
                "PROCEDURE",
                "MATERIAL",
                "DEADLINE",
                "RESPONSIBLE_ROLE",
                "PROCESS_CHOICE",
            },
        )
        self.assertEqual(
            {item["status"] for item in self.facets["binding_status"]},
            {"BOUND", "UNBOUND", "MISSING"},
        )

    def test_case_count_and_proposed_facets_match_review_scope(self) -> None:
        expected_facets = {
            "COV-007": {"MATERIAL", "RESPONSIBLE_ROLE"},
            "COV-008": {"DEADLINE"},
            "COV-009": {"PROCEDURE"},
            "COV-010": {"DEADLINE", "RESPONSIBLE_ROLE"},
            "V3U-004": {"PROCEDURE"},
            "V3U-006": set(),
            "V3U-009": {"PROCESS_CHOICE"},
        }
        self.assertEqual(set(self.by_case), set(expected_facets))
        for case_id, expected in expected_facets.items():
            with self.subTest(case_id=case_id):
                case = self.by_case[case_id]
                self.assertEqual(
                    {facet["facet"] for facet in case["proposed_facets"]},
                    expected,
                )
                self.assertEqual(case["review_status"], "pending_review")
                self.assertIsNone(case["accepted_facet_labels"])
                self.assertEqual(case["query"], self.records[case["record_line"] - 1]["query"])

    def test_facet_spans_are_exact_and_bindings_are_conservative(self) -> None:
        allowed = {facet["facet"] for facet in self.facets["allowed_facets"]}
        allowed_bindings = {item["status"] for item in self.facets["binding_status"]}
        for case in self.facets["cases"]:
            query = case["query"]
            for facet in case["proposed_facets"]:
                with self.subTest(case_id=case["case_id"], facet=facet["facet"]):
                    self.assertIn(facet["facet"], allowed)
                    self.assertEqual(facet["status"], "EXPRESSED")
                    self.assertIn(facet["binding"], allowed_bindings)
                    self.assertTrue(facet["source_spans"])
                    for span in facet["source_spans"]:
                        self.assertEqual(
                            query[span["start"] : span["end"]],
                            span["text"],
                        )
            if case["case_id"] in {"V3U-004", "V3U-009"}:
                self.assertEqual(
                    {facet["binding"] for facet in case["proposed_facets"]},
                    {"UNBOUND"},
                )

    def test_unresolved_outputs_are_carried_from_confirmed_records(self) -> None:
        for case in self.facets["cases"]:
            record = self.records[case["record_line"] - 1]
            expected = [
                {
                    "output_type": output["output_type"],
                    "reason": output["reason"],
                    "source_spans": output["source_spans"],
                }
                for output in record["missing_outputs"]
            ]
            self.assertEqual(case["unresolved_outputs"], expected)

    def test_sidecar_does_not_change_v1_record_contract(self) -> None:
        for line_number, payload in enumerate(self.records, start=1):
            with self.subTest(line_number=line_number):
                record = SemanticParseRecord.from_dict(payload)
                self.assertEqual(validate_record(record), ())
                self.assertNotIn("proposed_facets", payload)
                self.assertNotIn("request_facets", payload)


if __name__ == "__main__":
    unittest.main()
