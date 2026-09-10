from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.semantic_request_facets_adapter import (
    FacetAdapterError,
    load_semantic_request_facet_bundle,
)


ROOT = Path(__file__).resolve().parents[2]
RECORDS_PATH = ROOT / "docs" / "gate_v3" / "semantic-dev-v1-confirmed" / "records.jsonl"
ACCEPTED_PATH = ROOT / "docs" / "gate_v3" / "semantic-request-facets-v1-confirmed" / "accepted.json"
CONFIRMATION_PATH = (
    ROOT / "docs" / "gate_v3" / "semantic-request-facets-v1-confirmed" / "confirmation.json"
)
MANIFEST_PATH = ROOT / "docs" / "gate_v3" / "semantic-request-facets-v1-confirmed" / "manifest.json"


class SemanticRequestFacetsAdapterTests(unittest.TestCase):
    def _load(self, records=RECORDS_PATH, accepted=ACCEPTED_PATH, confirmation=CONFIRMATION_PATH):
        return load_semantic_request_facet_bundle(
            records,
            accepted,
            confirmation,
            manifest_path=MANIFEST_PATH,
            project_root=ROOT,
        )

    def _write_json_copy(self, directory: Path, source: Path, name: str, mutate) -> Path:
        payload = json.loads(source.read_text(encoding="utf-8"))
        mutate(payload)
        destination = directory / name
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    def test_loads_current_confirmed_bundle_and_preserves_uncertainty(self) -> None:
        bundle = self._load()

        self.assertEqual(bundle.dataset_version, "semantic-request-facets-v0.1-confirmed-1")
        self.assertEqual(bundle.source_dataset_version, "semantic-dev-v1-confirmed-1")
        self.assertEqual(len(bundle.records), 7)
        self.assertEqual(len(bundle.cases), 7)
        self.assertEqual(bundle.accepted_facet_instance_count, 8)
        self.assertTrue(bundle.manifest_hashes_verified)
        self.assertEqual(bundle.cases[0].case_id, "COV-007")
        self.assertEqual(bundle.cases[0].derived_binding_status, "BOUND")
        self.assertEqual(bundle.cases[4].derived_binding_status, "UNBOUND")
        self.assertEqual(bundle.cases[5].derived_binding_status, "MISSING")
        self.assertTrue(all(record.status.value == "PARTIAL" for record in bundle.records))
        self.assertTrue(all(record.missing_outputs for record in bundle.records))
        self.assertEqual(
            bundle.cases[5].preserved_unresolved_output_types,
            ("assertion_target",),
        )

    def test_rejects_unknown_sidecar_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            accepted = self._write_json_copy(
                Path(temporary),
                ACCEPTED_PATH,
                "accepted.json",
                lambda payload: payload.update({"unexpected": True}),
            )
            with self.assertRaises(FacetAdapterError) as caught:
                self._load(accepted=accepted)
        self.assertEqual(caught.exception.code, "UNKNOWN_FIELD")
        self.assertIn("accepted", caught.exception.path)

    def test_rejects_query_mismatch_before_joining(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            accepted = self._write_json_copy(
                Path(temporary),
                ACCEPTED_PATH,
                "accepted.json",
                lambda payload: payload["cases"][5].update({"query": "被篡改的 query"}),
            )
            with self.assertRaises(FacetAdapterError) as caught:
                self._load(accepted=accepted)
        self.assertEqual(caught.exception.code, "QUERY_MISMATCH")

    def test_rejects_duplicate_record_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            accepted = self._write_json_copy(
                Path(temporary),
                ACCEPTED_PATH,
                "accepted.json",
                lambda payload: payload["cases"][1].update({"record_line": 1}),
            )
            with self.assertRaises(FacetAdapterError) as caught:
                self._load(accepted=accepted)
        self.assertEqual(caught.exception.code, "DUPLICATE_RECORD_LINE")

    def test_rejects_source_record_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            records = Path(temporary) / "records.jsonl"
            records.write_text(RECORDS_PATH.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaises(FacetAdapterError) as caught:
                self._load(records=records)
        self.assertEqual(caught.exception.code, "SOURCE_RECORDS_HASH_MISMATCH")

    def test_rejects_confirmation_mapping_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            confirmation = self._write_json_copy(
                Path(temporary),
                CONFIRMATION_PATH,
                "confirmation.json",
                lambda payload: payload["accepted_mappings"][0]["accepted_facet_labels"].clear(),
            )
            with self.assertRaises(FacetAdapterError) as caught:
                self._load(confirmation=confirmation)
        self.assertEqual(caught.exception.code, "MAPPING_MISMATCH")

    def test_cli_emits_machine_readable_success(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "scripts.check_semantic_request_facets",
                "--records",
                str(RECORDS_PATH),
                "--accepted",
                str(ACCEPTED_PATH),
                "--confirmation",
                str(CONFIRMATION_PATH),
                "--manifest",
                str(MANIFEST_PATH),
                "--project-root",
                str(ROOT),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["record_count"], 7)
        self.assertEqual(summary["facet_case_count"], 7)
        self.assertEqual(summary["accepted_facet_instance_count"], 8)
        self.assertTrue(summary["manifest_hashes_verified"])

    def test_cli_returns_structured_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            accepted = self._write_json_copy(
                Path(temporary),
                ACCEPTED_PATH,
                "accepted.json",
                lambda payload: payload.update({"unexpected": True}),
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-m",
                    "scripts.check_semantic_request_facets",
                    "--records",
                    str(RECORDS_PATH),
                    "--accepted",
                    str(accepted),
                    "--confirmation",
                    str(CONFIRMATION_PATH),
                    "--manifest",
                    str(MANIFEST_PATH),
                    "--project-root",
                    str(ROOT),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["errors"][0]["code"], "UNKNOWN_FIELD")


if __name__ == "__main__":
    unittest.main()
