"""Freeze the reviewed semantic Facet challenge draft as a versioned sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.semantic_request_facets_adapter import load_semantic_request_facet_bundle

DATASET_VERSION = "semantic-facet-challenge-v1-confirmed-1"
SOURCE_DATASET_VERSION = "semantic-facet-challenge-v1-source-1"
SOURCE_PROPOSAL_PATH = Path("docs/gate_v3/semantic-request-facets-v1/facets.json")
PROMPT_PATH = Path("docs/gate_v3/semantic-facet-prompt-v1.txt")
ANCHORS: dict[str, dict[str, str]] = {
    "SFC-001": {"MATERIAL": "报销需要准备哪些凭证"},
    "SFC-002": {
        "MATERIAL": "需要准备哪些凭证",
        "DEADLINE": "最迟应在发生后多少天提交",
    },
    "SFC-003": {"PROCEDURE": "应按哪些步骤办理"},
    "SFC-004": {
        "PROCEDURE": "应按哪些步骤办理",
        "DEADLINE": "最迟应在返程后多少天提交",
    },
    "SFC-005": {"PROCEDURE": "住宿发票遗失后应该怎么办"},
    "SFC-006": {"PROCEDURE": "这个应该怎么办"},
    "SFC-007": {"MATERIAL": "差旅报销需要提交哪些材料"},
    "SFC-008": {"MATERIAL": "那个需要提交哪些材料"},
    "SFC-009": {"PROCESS_CHOICE": "应选择员工垫付报销流程，还是公司直接付款流程"},
    "SFC-010": {"PROCESS_CHOICE": "应选择之前提过的那个流程，还是另一个流程"},
    "SFC-011": {"PROCESS_CHOICE": "应选其中哪种"},
    "SFC-012": {"PROCESS_CHOICE": "应选其中哪种"},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def source_span(query: str, anchor: str) -> dict[str, object]:
    start = query.find(anchor)
    if start < 0:
        raise ValueError(f"anchor not found in query: {anchor!r}")
    return {"start": start, "end": start + len(anchor), "text": anchor}


def validate_draft(draft: dict[str, Any]) -> None:
    if draft.get("status") != "pending_review":
        raise ValueError("draft must still be pending_review before confirmation")
    cases = draft.get("cases")
    if not isinstance(cases, list) or len(cases) != 12:
        raise ValueError("draft must contain exactly 12 cases")
    if len({case.get("case_id") for case in cases}) != 12:
        raise ValueError("draft case IDs must be unique")
    if len({case.get("query") for case in cases}) != 12:
        raise ValueError("draft queries must be unique")
    for case in cases:
        if case.get("review_status") != "pending_review" or case.get("accepted_facets") is not None:
            raise ValueError("draft cases must not contain accepted labels")
        case_id = case.get("case_id")
        if case_id not in ANCHORS:
            raise ValueError(f"missing source-span anchors for {case_id!r}")
        for facet in case.get("proposed_facets", []):
            name = facet.get("facet")
            if name not in ANCHORS[case_id]:
                raise ValueError(f"missing source-span anchor for {case_id!r}/{name!r}")


def build_records(cases: list[dict[str, Any]]) -> list[dict[str, object]]:
    records = []
    for case in cases:
        query = case["query"]
        records.append(
            {
                "schema_version": "1.0",
                "query": query,
                "status": "PARTIAL",
                "parser_id": None,
                "parser_revision": None,
                "nodes": [],
                "edges": [],
                "scopes": [],
                "missing_outputs": [
                    {
                        "output_type": "request_facet",
                        "reason": "challenge_fixture: request Facet is evaluated by the confirmed sidecar",
                        "source_spans": [{"start": 0, "end": len(query), "text": query}],
                    }
                ],
            }
        )
    return records


def build_accepted(cases: list[dict[str, Any]], proposal: dict[str, Any]) -> dict[str, object]:
    accepted_cases = []
    accepted_instance_count = 0
    for line_number, case in enumerate(cases, start=1):
        accepted_facets = []
        for facet in case["proposed_facets"]:
            name = facet["facet"]
            accepted_facets.append(
                {
                    "facet": name,
                    "status": "EXPRESSED",
                    "binding": facet["binding"],
                    "source_spans": [source_span(case["query"], ANCHORS[case["case_id"]][name])],
                    "rationale": case["rationale"],
                }
            )
        accepted_instance_count += len(accepted_facets)
        accepted_cases.append(
            {
                "case_id": case["case_id"],
                "record_line": line_number,
                "query": case["query"],
                "accepted_facets": accepted_facets,
                "preserved_unresolved_output_types": ["request_facet"],
                "note": f"{case['category']}；{case['rationale']}",
            }
        )
    return {
        "schema_version": "1.0",
        "dataset_version": DATASET_VERSION,
        "status": "user_confirmed",
        "source_proposal_version": proposal["facet_version"],
        "source_dataset_version": SOURCE_DATASET_VERSION,
        "sample_count": len(accepted_cases),
        "accepted_case_count": len(accepted_cases),
        "accepted_facet_instance_count": accepted_instance_count,
        "independent_test_set": False,
        "numeric_resume_ready": False,
        "model_inference": False,
        "retrieval_executed": False,
        "parser_runtime_integration": False,
        "allowed_facets": proposal["allowed_facets"],
        "binding_statuses": proposal["binding_status"],
        "cases": accepted_cases,
    }


def build_confirmation(
    accepted: dict[str, object],
    proposal_path: Path,
    records_path: Path,
) -> dict[str, object]:
    cases = accepted["cases"]
    mappings = [
        {
            "case_id": case["case_id"],
            "accepted_facet_labels": [facet["facet"] for facet in case["accepted_facets"]],
            "binding_by_facet": {
                facet["facet"]: facet["binding"] for facet in case["accepted_facets"]
            },
        }
        for case in cases
    ]
    return {
        "schema_version": "1.0",
        "confirmation_version": "semantic-facet-challenge-confirmation-v1",
        "dataset_version": DATASET_VERSION,
        "source_proposal_version": accepted["source_proposal_version"],
        "source_proposal_sha256": sha256(proposal_path),
        "source_dataset_version": SOURCE_DATASET_VERSION,
        "source_records_sha256": sha256(records_path),
        "status": "user_confirmed",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "confirmation_source": "current conversation; user reply ‘确认允许’ confirmed the reviewed draft labels and permitted sending the 12 queries to api.deepseek.com",
        "confirmation_scope": [
            "six paired targeted challenge groups",
            "twelve query texts and their proposed Facet labels",
            "BOUND and UNBOUND bindings with source spans",
            "targeted challenge scope; not an independent test set",
        ],
        "case_ids_in_dataset_order": [case["case_id"] for case in cases],
        "accepted_case_count": len(cases),
        "rejected_case_count": 0,
        "pending_case_count": 0,
        "confirmed_definition_count": len(accepted["allowed_facets"]),
        "accepted_facet_instance_count": accepted["accepted_facet_instance_count"],
        "records_changed": False,
        "draft_preserved": True,
        "partial_records_remain_partial": True,
        "unresolved_outputs_preserved": True,
        "historical_labels_changed": False,
        "model_inference": False,
        "semantic_accuracy": None,
        "independent_double_annotation": False,
        "independent_test_set": False,
        "numeric_resume_ready": False,
        "retrieval_executed": False,
        "parser_runtime_integration": False,
        "accepted_mappings": mappings,
        "scope_boundary": "This is a user-confirmed targeted challenge set designed from previous development errors. It is not independently double-annotated and does not establish an independent test-set score, semantic accuracy, retrieval gain, or production behavior.",
    }


def freeze_dataset(root: Path, output: Path) -> Path:
    root = root.resolve()
    draft_path = root / "docs/gate_v3/semantic-facet-challenge-v1-draft/cases.json"
    proposal_path = root / SOURCE_PROPOSAL_PATH
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    validate_draft(draft)
    resolved_output = _rooted_path(root, output)
    resolved_output.mkdir(parents=True, exist_ok=False)
    records_path = resolved_output / "records.jsonl"
    records = build_records(draft["cases"])
    records_path.write_text(
        "\n".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records
        )
        + "\n",
        encoding="utf-8",
    )
    accepted = build_accepted(draft["cases"], proposal)
    accepted_path = resolved_output / "accepted.json"
    write_json(accepted_path, accepted)
    confirmation = build_confirmation(accepted, proposal_path, records_path)
    confirmation_path = resolved_output / "confirmation.json"
    write_json(confirmation_path, confirmation)
    readme_path = resolved_output / "README.md"
    readme_path.write_text(
        "# Semantic Facet Challenge v1 Confirmed\n\n"
        "状态：user_confirmed。该数据集是基于已有开发错误设计的定向挑战集，不是独立测试集。\n\n"
        "本目录的 records.jsonl 是用于 adapter 对齐的 PARTIAL challenge fixture；accepted.json 保存用户确认的 Facet 与 source spans；confirmation.json 保存确认范围与 provenance。原始草案仍保存在 `docs/gate_v3/semantic-facet-challenge-v1-draft/`。\n\n"
        "本数据集包含 12 条 query、6 组对照和 14 个 Facet 实例，覆盖时间条件/时间询问、明确对象/省略对象、明确候选流程/模糊指代。当前不包含模型推理、检索、运行时接入或语义准确率。\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0",
        "dataset_version": DATASET_VERSION,
        "source_dataset_version": SOURCE_DATASET_VERSION,
        "status": "user_confirmed",
        "sample_count": len(records),
        "independent_test_set": False,
        "numeric_resume_ready": False,
        "model_inference": False,
        "retrieval_executed": False,
        "parser_runtime_integration": False,
        "source_files": [
            {"path": draft_path.relative_to(root).as_posix(), "sha256": sha256(draft_path)},
            {"path": SOURCE_PROPOSAL_PATH.as_posix(), "sha256": sha256(proposal_path)},
            {"path": PROMPT_PATH.as_posix(), "sha256": sha256(root / PROMPT_PATH)},
        ],
        "deliverables": [
            {"path": records_path.relative_to(root).as_posix(), "sha256": sha256(records_path)},
            {"path": accepted_path.relative_to(root).as_posix(), "sha256": sha256(accepted_path)},
            {
                "path": confirmation_path.relative_to(root).as_posix(),
                "sha256": sha256(confirmation_path),
            },
            {"path": readme_path.relative_to(root).as_posix(), "sha256": sha256(readme_path)},
        ],
    }
    manifest_path = resolved_output / "manifest.json"
    write_json(manifest_path, manifest)
    load_semantic_request_facet_bundle(
        records_path,
        accepted_path,
        confirmation_path,
        manifest_path=manifest_path,
        project_root=root,
    )
    return resolved_output


def _rooted_path(root: Path, path: Path) -> Path:
    return (root / path if not path.is_absolute() else path).resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/gate_v3/semantic-facet-challenge-v1-confirmed"),
    )
    args = parser.parse_args()
    output = freeze_dataset(args.project_root, args.output)
    print(json.dumps({"status": "passed", "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
