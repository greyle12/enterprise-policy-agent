from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scripts.check_semantic_parse_records import check_records
from scripts.semantic_parse_contract import (
    CONTRACT_SCHEMA_VERSION,
    ContractDecodeError,
    MissingSemanticOutput,
    NodeType,
    ParseStatus,
    RelationType,
    SemanticAct,
    SemanticEdge,
    SemanticNode,
    SemanticParseRecord,
    SemanticScope,
    ScopeType,
    Span,
    _find_cycle,
    validate_record,
)


def _span(query: str, text: str, start: int = 0) -> Span:
    span_start = query.index(text, start)
    return Span(span_start, span_start + len(text), text)


class SemanticParseContractTests(unittest.TestCase):
    def _valid_record(self) -> SemanticParseRecord:
        query = "有人说“普通费用不需要财务复核”，请核实这项说法；我不要求跳过审批。"
        reported_text = "普通费用不需要财务复核"
        verify_text = "请核实这项说法"
        deny_text = "我不要求跳过审批"
        request_scope_text = "请核实这项说法；我不要求跳过审批"

        reported = SemanticNode(
            node_id="assertion-1",
            node_type=NodeType.REPORTED_ASSERTION,
            text=reported_text,
            source_spans=(_span(query, reported_text),),
            act=SemanticAct.REPORT_ASSERTION,
            scope_id="scope-quoted",
        )
        verify = SemanticNode(
            node_id="request-verify",
            node_type=NodeType.USER_REQUEST,
            text=verify_text,
            source_spans=(_span(query, verify_text),),
            act=SemanticAct.VERIFY_ASSERTION,
            scope_id="scope-request",
        )
        deny = SemanticNode(
            node_id="request-deny",
            node_type=NodeType.USER_REQUEST,
            text=deny_text,
            source_spans=(_span(query, deny_text),),
            act=SemanticAct.DENY_REQUEST,
            scope_id="scope-request",
        )
        return SemanticParseRecord(
            query=query,
            status=ParseStatus.COMPLETE,
            nodes=(reported, verify, deny),
            edges=(
                SemanticEdge(
                    edge_id="edge-verify",
                    source="request-verify",
                    target="assertion-1",
                    relation=RelationType.TARGETS,
                ),
            ),
            scopes=(
                SemanticScope(
                    scope_id="scope-quoted",
                    scope_type=ScopeType.REPORTED_ASSERTION,
                    source_spans=(_span(query, reported_text),),
                    node_ids=("assertion-1",),
                ),
                SemanticScope(
                    scope_id="scope-request",
                    scope_type=ScopeType.USER_REQUEST,
                    source_spans=(_span(query, request_scope_text),),
                    node_ids=("request-verify", "request-deny"),
                ),
            ),
            schema_version=CONTRACT_SCHEMA_VERSION,
            parser_id="fixture",
            parser_revision="test-1",
        )

    def test_valid_record_round_trips_through_json_shape(self) -> None:
        record = self._valid_record()

        self.assertEqual(validate_record(record), ())
        payload = record.to_dict()
        decoded = SemanticParseRecord.from_dict(payload)

        self.assertEqual(decoded, record)
        self.assertEqual(payload["schema_version"], CONTRACT_SCHEMA_VERSION)
        self.assertEqual(payload["nodes"][0]["node_type"], "REPORTED_ASSERTION")

    def test_user_request_and_reported_assertion_acts_are_distinct(self) -> None:
        record = self._valid_record()
        reported = replace(
            record.nodes[0],
            node_type=NodeType.USER_REQUEST,
        )
        verify = replace(
            record.nodes[1],
            node_type=NodeType.REPORTED_ASSERTION,
        )
        invalid = replace(record, nodes=(reported, verify, record.nodes[2]))

        codes = {issue.code for issue in validate_record(invalid)}

        self.assertIn("ACT_NODE_TYPE_MISMATCH", codes)

    def test_source_spans_must_match_original_query(self) -> None:
        record = self._valid_record()
        mismatch = replace(
            record.nodes[0],
            source_spans=(Span(3, 3 + len(record.nodes[0].text), "错误文本"),),
        )
        out_of_bounds = replace(
            record.nodes[1],
            source_spans=(Span(-1, 4, record.nodes[1].text),),
        )

        mismatch_codes = {
            issue.code
            for issue in validate_record(
                replace(record, nodes=(mismatch, record.nodes[1], record.nodes[2]))
            )
        }
        bounds_codes = {
            issue.code
            for issue in validate_record(
                replace(
                    record,
                    nodes=(record.nodes[0], out_of_bounds, record.nodes[2]),
                )
            )
        }

        self.assertIn("SPAN_TEXT_MISMATCH", mismatch_codes)
        self.assertIn("SPAN_OUT_OF_BOUNDS", bounds_codes)

    def test_dangling_edge_reference_is_rejected(self) -> None:
        record = self._valid_record()
        dangling_edge = SemanticEdge(
            edge_id="edge-dangling",
            source="request-verify",
            target="node-that-does-not-exist",
            relation=RelationType.TARGETS,
        )

        codes = {issue.code for issue in validate_record(replace(record, edges=(dangling_edge,)))}

        self.assertIn("DANGLING_EDGE_TARGET", codes)

    def test_relation_cycle_is_rejected(self) -> None:
        record = self._valid_record()
        cyclic_edges = (
            SemanticEdge(
                edge_id="edge-a",
                source="request-verify",
                target="assertion-1",
                relation=RelationType.TARGETS,
            ),
            SemanticEdge(
                edge_id="edge-b",
                source="assertion-1",
                target="request-verify",
                relation=RelationType.QUOTES,
            ),
        )

        codes = {issue.code for issue in validate_record(replace(record, edges=cyclic_edges))}

        self.assertIn("RELATION_CYCLE", codes)

    def test_scope_parent_cycle_is_rejected(self) -> None:
        record = self._valid_record()
        nodes_without_scope = tuple(replace(node, scope_id=None) for node in record.nodes)
        scopes = (
            SemanticScope(
                scope_id="scope-a",
                scope_type=ScopeType.QUERY,
                source_spans=(_span(record.query, "有人说"),),
                node_ids=("assertion-1",),
                parent_scope_id="scope-b",
            ),
            SemanticScope(
                scope_id="scope-b",
                scope_type=ScopeType.UNKNOWN,
                source_spans=(_span(record.query, "请核实"),),
                node_ids=("request-verify",),
                parent_scope_id="scope-a",
            ),
        )

        codes = {
            issue.code
            for issue in validate_record(replace(record, nodes=nodes_without_scope, scopes=scopes))
        }

        self.assertIn("SCOPE_CYCLE", codes)

    def test_partial_record_preserves_missing_output(self) -> None:
        query = "请判断这句话。"
        missing = MissingSemanticOutput(
            output_type="assertion_target",
            reason="ambiguous_reference",
            source_spans=(_span(query, "这句话"),),
        )
        record = SemanticParseRecord(
            query=query,
            status=ParseStatus.PARTIAL,
            missing_outputs=(missing,),
        )

        self.assertEqual(validate_record(record), ())
        decoded = SemanticParseRecord.from_dict(record.to_dict())

        self.assertEqual(decoded.missing_outputs, (missing,))
        self.assertEqual(decoded.status, ParseStatus.PARTIAL)

    def test_missing_output_and_status_must_agree(self) -> None:
        record = self._valid_record()
        complete_with_missing = replace(
            record,
            missing_outputs=(
                MissingSemanticOutput(
                    output_type="condition",
                    reason="not_extracted",
                ),
            ),
        )
        partial_without_missing = replace(record, status=ParseStatus.PARTIAL)

        complete_codes = {issue.code for issue in validate_record(complete_with_missing)}
        partial_codes = {issue.code for issue in validate_record(partial_without_missing)}

        self.assertIn("MISSING_OUTPUT_STATUS_MISMATCH", complete_codes)
        self.assertIn("MISSING_OUTPUTS_REQUIRED", partial_codes)

    def test_decoder_requires_explicit_contract_fields(self) -> None:
        payload = self._valid_record().to_dict()
        del payload["missing_outputs"]

        with self.assertRaises(ContractDecodeError):
            SemanticParseRecord.from_dict(payload)

    def test_decoder_rejects_unknown_fields_at_every_object_level(self) -> None:
        paths = (
            (),
            ("nodes", 0),
            ("edges", 0),
            ("scopes", 0),
            ("missing_outputs", 0),
            ("nodes", 0, "source_spans", 0),
            ("edges", 0, "source_spans", 0),
            ("scopes", 0, "source_spans", 0),
            ("missing_outputs", 0, "source_spans", 0),
        )
        for path in paths:
            with self.subTest(path=path):
                record = self._valid_record()
                evidence = record.nodes[0].source_spans
                record = replace(
                    record,
                    status=ParseStatus.PARTIAL,
                    edges=(replace(record.edges[0], source_spans=evidence),),
                    missing_outputs=(MissingSemanticOutput("target", "ambiguous", evidence),),
                )
                payload = record.as_dict()
                target = payload
                expected_path = "record"
                for part in path:
                    target = target[part]
                    expected_path += f"[{part}]" if isinstance(part, int) else f".{part}"
                target["unexpected_field"] = "must not disappear"
                with self.assertRaises(ContractDecodeError) as caught:
                    SemanticParseRecord.from_dict(payload)
                self.assertIn(expected_path, str(caught.exception))
                self.assertIn("unexpected_field", str(caught.exception))

    def test_checker_counts_unknown_field_and_continues(self) -> None:
        valid = self._valid_record().as_dict()
        unknown = dict(valid, missing_output=[])
        summary = check_records([json.dumps(unknown), json.dumps(valid)])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(summary["valid_record_count"], 1)
        self.assertEqual(summary["invalid_record_count"], 1)
        self.assertEqual(summary["errors"][0]["line"], 1)
        self.assertEqual(summary["errors"][0]["code"], "CONTRACT_DECODE_ERROR")

    def test_jsonl_checker_reports_valid_and_invalid_records(self) -> None:
        valid_payload = self._valid_record().to_dict()
        invalid_payload = replace(
            self._valid_record(),
            status=ParseStatus.FAILED,
        ).to_dict()

        summary = check_records(
            [
                json.dumps(valid_payload, ensure_ascii=False),
                json.dumps(invalid_payload, ensure_ascii=False),
            ]
        )

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(summary["valid_record_count"], 1)
        self.assertEqual(summary["invalid_record_count"], 1)
        self.assertTrue(
            any(error["code"] == "MISSING_OUTPUTS_REQUIRED" for error in summary["errors"])
        )

    def test_checker_counts_all_failure_stages_and_preserves_lines(self) -> None:
        valid = json.dumps(self._valid_record().to_dict())
        invalid = json.dumps(
            replace(self._valid_record(), query="", status=ParseStatus.FAILED).to_dict()
        )
        summary = check_records(["\n", valid, "{bad json", "{}", invalid, "  ", valid])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["record_count"], 5)
        self.assertEqual(summary["valid_record_count"], 2)
        self.assertEqual(summary["invalid_record_count"], 3)
        self.assertEqual(
            summary["record_count"], summary["valid_record_count"] + summary["invalid_record_count"]
        )
        errors = summary["errors"]
        self.assertEqual(errors[0]["line"], 3)
        self.assertEqual(errors[0]["code"], "INVALID_JSON")
        self.assertTrue(errors[0]["message"])
        self.assertEqual(errors[1]["line"], 4)
        self.assertEqual(errors[1]["code"], "CONTRACT_DECODE_ERROR")
        self.assertTrue(all(error["line"] == 5 for error in errors[2:]))
        self.assertGreater(summary["error_count"], summary["invalid_record_count"])

    def test_checker_all_undecodable_records_are_counted(self) -> None:
        summary = check_records(["{", "{}", "null"])
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["record_count"], 3)
        self.assertEqual(summary["valid_record_count"], 0)
        self.assertEqual(summary["invalid_record_count"], 3)
        self.assertEqual(summary["error_count"], 3)
        self.assertNotIn("NO_RECORDS", [error["code"] for error in summary["errors"]])

    def test_checker_empty_input_is_not_a_failed_record(self) -> None:
        for lines in ([], ["", "  ", "\n", "\t"]):
            with self.subTest(lines=lines):
                summary = check_records(lines)
                self.assertEqual(summary["status"], "failed")
                for key in ("record_count", "valid_record_count", "invalid_record_count"):
                    self.assertEqual(summary[key], 0)
                self.assertEqual(summary["error_count"], 1)
                self.assertEqual(summary["errors"][0]["code"], "NO_RECORDS")
                self.assertIsNone(summary["errors"][0]["line"])


class SemanticGraphTests(unittest.TestCase):
    def test_long_relation_and_scope_chains(self) -> None:
        count = 2000
        nodes = tuple(
            SemanticNode(str(i), NodeType.ENTITY, "x", (Span(0, 1, "x"),)) for i in range(count)
        )
        edges = tuple(
            SemanticEdge(str(i), str(i), str(i + 1), RelationType.REFERS_TO)
            for i in range(count - 1)
        )
        scopes = tuple(
            SemanticScope(
                str(i),
                ScopeType.QUERY,
                (Span(0, 1, "x"),),
                (),
                str(i + 1) if i < count - 1 else None,
            )
            for i in range(count)
        )
        record = SemanticParseRecord(
            "x", ParseStatus.COMPLETE, nodes=nodes, edges=edges, scopes=scopes
        )
        self.assertEqual(validate_record(record), ())
        cyclic = replace(
            record,
            edges=edges + (SemanticEdge("back", str(count - 1), "0", RelationType.REFERS_TO),),
            scopes=scopes[:-1] + (replace(scopes[-1], parent_scope_id="0"),),
        )
        self.assertEqual(
            {issue.code for issue in validate_record(cyclic)}, {"RELATION_CYCLE", "SCOPE_CYCLE"}
        )

    def test_cycle_selection_ignores_insertion_order(self) -> None:
        graph = {"a": ["c", "b"], "b": ["a"], "c": ["a"]}
        reordered = {key: list(reversed(graph[key])) for key in reversed(graph)}
        self.assertEqual(_find_cycle(graph), ("a", "b", "a"))
        self.assertEqual(_find_cycle(reordered), ("a", "b", "a"))

    def test_shared_descendant_is_not_cycle(self) -> None:
        self.assertIsNone(_find_cycle({"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}))
        self.assertEqual(_find_cycle({"a": ["a"]}), ("a", "a"))


class SemanticCheckerCLITests(unittest.TestCase):
    def run_cli(self, content: bytes | None) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            if content is not None:
                path.write_bytes(content)
            result = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-m",
                    "scripts.check_semantic_parse_records",
                    str(path),
                ],
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def test_utf8_and_bom_files(self) -> None:
        record = SemanticParseRecord(
            query="请核实这句话。",
            status=ParseStatus.PARTIAL,
            missing_outputs=(MissingSemanticOutput("target", "ambiguous_reference"),),
        )
        for encoding in ("utf-8", "utf-8-sig"):
            with self.subTest(encoding=encoding):
                code, summary = self.run_cli(
                    json.dumps(record.as_dict(), ensure_ascii=False).encode(encoding)
                )
                self.assertEqual(code, 0)
                self.assertEqual(summary["status"], "passed")
                self.assertEqual(summary["valid_record_count"], 1)
                self.assertEqual(summary["records_with_missing_outputs"], 1)

    def test_mixed_file_keeps_counts_and_line_numbers(self) -> None:
        record = SemanticParseRecord(
            query="example",
            status=ParseStatus.FAILED,
            missing_outputs=(MissingSemanticOutput("parse", "unavailable"),),
        )
        content = "\n" + json.dumps(record.as_dict()) + "\n{\n{}\n"
        code, summary = self.run_cli(content.encode("utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["record_count"], 3)
        self.assertEqual(summary["valid_record_count"], 1)
        self.assertEqual(summary["invalid_record_count"], 2)
        self.assertEqual([error["line"] for error in summary["errors"]], [3, 4])

    def test_missing_file_returns_json_error(self) -> None:
        code, summary = self.run_cli(None)
        self.assertEqual(code, 1)
        self.assertEqual(summary["errors"][0]["code"], "INPUT_READ_ERROR")

    def test_invalid_utf8_returns_json_error(self) -> None:
        code, summary = self.run_cli(b"\xff\n")
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["errors"][0]["code"], "INPUT_ENCODING_ERROR")
        self.assertIsNone(summary["errors"][0]["line"])
        self.assertEqual(summary["record_count"], 0)


if __name__ == "__main__":
    unittest.main()
