from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.semantic_parse_contract import SemanticParseRecord, validate_record


ROOT = Path(__file__).resolve().parents[2]
DRAFT_PATH = ROOT / "docs" / "gate_v3" / "semantic-request-facets-v1" / "facets.json"
CONFIRMED_PATH = (
    ROOT / "docs" / "gate_v3" / "semantic-request-facets-v1-confirmed" / "accepted.json"
)
CONFIRMATION_PATH = (
    ROOT / "docs" / "gate_v3" / "semantic-request-facets-v1-confirmed" / "confirmation.json"
)
RECORDS_PATH = ROOT / "docs" / "gate_v3" / "semantic-dev-v1-confirmed" / "records.jsonl"


class ConfirmedSemanticRequestFacetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
        cls.confirmed = json.loads(CONFIRMED_PATH.read_text(encoding="utf-8"))
        cls.confirmation = json.loads(CONFIRMATION_PATH.read_text(encoding="utf-8"))
        cls.records = [
            json.loads(line)
            for line in RECORDS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_confirmation_status_and_provenance_are_explicit(self) -> None:
        self.assertEqual(
            self.confirmed["dataset_version"],
            "semantic-request-facets-v0.1-confirmed-1",
        )
        self.assertEqual(self.confirmed["status"], "user_confirmed")
        self.assertEqual(self.confirmation["status"], "user_confirmed")
        self.assertEqual(self.confirmed["sample_count"], 7)
        self.assertEqual(self.confirmed["accepted_case_count"], 7)
        self.assertEqual(self.confirmed["accepted_facet_instance_count"], 8)
        self.assertEqual(self.confirmation["accepted_case_count"], 7)
        self.assertEqual(self.confirmation["rejected_case_count"], 0)
        self.assertEqual(self.confirmation["pending_case_count"], 0)
        self.assertEqual(self.confirmation["confirmed_definition_count"], 5)
        self.assertTrue(self.confirmation["draft_preserved"])
        self.assertFalse(self.confirmation["records_changed"])
        self.assertFalse(self.confirmation["model_inference"])
        self.assertFalse(self.confirmation["independent_test_set"])
        self.assertFalse(self.confirmation["numeric_resume_ready"])

    def test_confirmed_definitions_and_binding_vocabulary_match_draft(self) -> None:
        self.assertEqual(
            self.confirmed["allowed_facets"],
            self.draft["allowed_facets"],
        )
        self.assertEqual(
            self.confirmed["binding_statuses"],
            self.draft["binding_status"],
        )

    def test_confirmed_cases_match_proposed_facets_and_confirmation(self) -> None:
        draft_by_case = {case["case_id"]: case for case in self.draft["cases"]}
        confirmed_by_case = {case["case_id"]: case for case in self.confirmed["cases"]}
        confirmation_by_case = {
            case["case_id"]: case for case in self.confirmation["accepted_mappings"]
        }
        self.assertEqual(set(confirmed_by_case), set(draft_by_case))
        self.assertEqual(set(confirmation_by_case), set(draft_by_case))

        for case_id, draft_case in draft_by_case.items():
            with self.subTest(case_id=case_id):
                confirmed_case = confirmed_by_case[case_id]
                confirmation = confirmation_by_case[case_id]
                self.assertEqual(confirmed_case["record_line"], draft_case["record_line"])
                self.assertEqual(confirmed_case["query"], draft_case["query"])
                expected_facets = [
                    {
                        "facet": facet["facet"],
                        "status": facet["status"],
                        "binding": facet["binding"],
                        "source_spans": facet["source_spans"],
                        "rationale": facet["rationale"],
                    }
                    for facet in draft_case["proposed_facets"]
                ]
                self.assertEqual(confirmed_case["accepted_facets"], expected_facets)
                self.assertEqual(
                    confirmation["accepted_facet_labels"],
                    [facet["facet"] for facet in expected_facets],
                )
                self.assertEqual(
                    confirmation["binding_by_facet"],
                    {facet["facet"]: facet["binding"] for facet in expected_facets},
                )
                self.assertEqual(
                    confirmed_case["preserved_unresolved_output_types"],
                    [output["output_type"] for output in draft_case["unresolved_outputs"]],
                )

    def test_spans_and_source_records_are_preserved(self) -> None:
        for case in self.confirmed["cases"]:
            record = self.records[case["record_line"] - 1]
            with self.subTest(case_id=case["case_id"]):
                self.assertEqual(case["query"], record["query"])
                for facet in case["accepted_facets"]:
                    for span in facet["source_spans"]:
                        self.assertEqual(
                            case["query"][span["start"] : span["end"]],
                            span["text"],
                        )
                self.assertEqual(
                    case["preserved_unresolved_output_types"],
                    [output["output_type"] for output in record["missing_outputs"]],
                )

    def test_v1_record_contract_and_draft_remain_unchanged(self) -> None:
        for line_number, payload in enumerate(self.records, start=1):
            with self.subTest(line_number=line_number):
                record = SemanticParseRecord.from_dict(payload)
                self.assertEqual(validate_record(record), ())
                self.assertNotIn("accepted_facets", payload)
                self.assertNotIn("request_facets", payload)
        self.assertEqual(self.draft["status"], "proposal_pending_confirmation")
        self.assertEqual(self.draft["confirmed_facet_count"], 0)


if __name__ == "__main__":
    unittest.main()
