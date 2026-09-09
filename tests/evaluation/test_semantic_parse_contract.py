from __future__ import annotations

import json
import unittest
from dataclasses import replace

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


if __name__ == "__main__":
    unittest.main()
