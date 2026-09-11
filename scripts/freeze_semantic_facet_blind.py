"""Freeze the reviewed blind semantic Facet candidate set as a sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.semantic_request_facets_adapter import load_semantic_request_facet_bundle

DATASET_VERSION = "semantic-facet-blind-v2-confirmed-1"
SOURCE_DATASET_VERSION = "semantic-facet-blind-v2-source-1"
DRAFT_FOLDER = Path("docs/gate_v3/semantic-facet-blind-v2-draft")
PROPOSAL_PATH = Path("docs/gate_v3/semantic-request-facets-v1/facets.json")
PROMPT_PATH = Path("docs/gate_v3/semantic-facet-prompt-v1.txt")
FACETS = frozenset({"PROCEDURE", "MATERIAL", "DEADLINE", "RESPONSIBLE_ROLE", "PROCESS_CHOICE"})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_draft(root: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    folder = root / DRAFT_FOLDER
    questions = [
        json.loads(line)
        for line in (folder / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    review = json.loads((folder / "review.json").read_text(encoding="utf-8"))
    return questions, review


def validate_draft(questions: list[dict[str, str]], review: dict[str, Any]) -> None:
    if review.get("status") != "pending_review":
        raise ValueError("review package must still be pending_review")
    if review.get("sample_count") != 18 or len(questions) != 18:
        raise ValueError("review package must contain exactly 18 questions")
    if any(
        review.get(field) is not False
        for field in ("independent_test_set", "numeric_resume_ready", "model_inference")
    ):
        raise ValueError("review boundary flags are inconsistent")

    question_ids = [question.get("case_id") for question in questions]
    if len(set(question_ids)) != 18 or any(
        set(question) != {"case_id", "query"} for question in questions
    ):
        raise ValueError("questions must have unique IDs and only model-visible fields")
    if [case.get("case_id") for case in review.get("cases", [])] != question_ids:
        raise ValueError("review labels are not aligned with questions")

    for question, case in zip(questions, review["cases"], strict=True):
        query = question.get("query", "")
        if not query.strip():
            raise ValueError("query must not be empty")
        if case.get("review_status") != "pending_review" or case.get("accepted_facets") is not None:
            raise ValueError("draft cases must not contain accepted labels")
        seen: set[str] = set()
        for facet in case.get("proposed_facets", []):
            name = facet.get("facet")
            if name not in FACETS or name in seen:
                raise ValueError(f"invalid or duplicate facet in {case['case_id']}")
            if facet.get("binding") not in {"BOUND", "UNBOUND"}:
                raise ValueError(f"invalid binding in {case['case_id']}/{name}")
            span = facet.get("span")
            if not isinstance(span, dict) or set(span) != {"start", "end", "text"}:
                raise ValueError(f"invalid span in {case['case_id']}/{name}")
            if not (
                isinstance(span["start"], int)
                and isinstance(span["end"], int)
                and 0 <= span["start"] < span["end"] <= len(query)
                and query[span["start"] : span["end"]] == span["text"]
            ):
                raise ValueError(f"source span does not round-trip in {case['case_id']}/{name}")
            seen.add(name)


def build_records(questions: list[dict[str, str]]) -> list[dict[str, object]]:
    return [
        {
            "schema_version": "1.0",
            "query": question["query"],
            "status": "PARTIAL",
            "parser_id": None,
            "parser_revision": None,
            "nodes": [],
            "edges": [],
            "scopes": [],
            "missing_outputs": [
                {
                    "output_type": "request_facet",
                    "reason": "blind_challenge_fixture: request Facet is evaluated by the confirmed sidecar",
                    "source_spans": [
                        {"start": 0, "end": len(question["query"]), "text": question["query"]}
                    ],
                }
            ],
        }
        for question in questions
    ]


def build_accepted(
    questions: list[dict[str, str]], review: dict[str, Any], proposal: dict[str, Any]
) -> dict[str, object]:
    cases = []
    instance_count = 0
    for line_number, (question, review_case) in enumerate(
        zip(questions, review["cases"], strict=True), start=1
    ):
        accepted_facets = [
            {
                "facet": facet["facet"],
                "status": "EXPRESSED",
                "binding": facet["binding"],
                "source_spans": [facet["span"]],
                "rationale": review_case["rationale"],
            }
            for facet in review_case["proposed_facets"]
        ]
        instance_count += len(accepted_facets)
        cases.append(
            {
                "case_id": question["case_id"],
                "record_line": line_number,
                "query": question["query"],
                "accepted_facets": accepted_facets,
                "preserved_unresolved_output_types": ["request_facet"],
                "note": f"{review_case['category']}；{review_case['rationale']}",
            }
        )
    return {
        "schema_version": "1.0",
        "dataset_version": DATASET_VERSION,
        "status": "user_confirmed",
        "source_proposal_version": proposal["facet_version"],
        "source_dataset_version": SOURCE_DATASET_VERSION,
        "sample_count": len(cases),
        "accepted_case_count": len(cases),
        "accepted_facet_instance_count": instance_count,
        "independent_test_set": False,
        "numeric_resume_ready": False,
        "model_inference": False,
        "retrieval_executed": False,
        "parser_runtime_integration": False,
        "allowed_facets": proposal["allowed_facets"],
        "binding_statuses": proposal["binding_status"],
        "cases": cases,
    }


def build_confirmation(
    accepted: dict[str, object], proposal_path: Path, records_path: Path
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
        "confirmation_version": "semantic-facet-blind-confirmation-v1",
        "dataset_version": DATASET_VERSION,
        "source_proposal_version": accepted["source_proposal_version"],
        "source_proposal_sha256": sha256(proposal_path),
        "source_dataset_version": SOURCE_DATASET_VERSION,
        "source_records_sha256": sha256(records_path),
        "status": "user_confirmed",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "confirmation_source": "current conversation; user reply ‘接受，并允许执行上述冻结与验收步骤’ confirmed the reviewed 18 labels",
        "confirmation_scope": [
            "eighteen query texts and their proposed Facet labels",
            "BOUND and UNBOUND bindings with source spans",
            "two confirmed empty Facet outputs",
            "targeted blind-review scope; independent test-set status remains false",
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
        "scope_boundary": "This is a user-confirmed targeted blind-review candidate set authored after inspecting previous development errors. It is not independently double-annotated and does not establish a model score or production behavior.",
    }


def freeze_dataset(root: Path, output: Path) -> Path:
    root = root.resolve()
    question_path = root / DRAFT_FOLDER / "questions.jsonl"
    review_path = root / DRAFT_FOLDER / "review.json"
    proposal_path = root / PROPOSAL_PATH
    prompt_path = root / PROMPT_PATH
    questions, review = load_draft(root)
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    validate_draft(questions, review)
    resolved_output = (root / output if not output.is_absolute() else output).resolve()
    resolved_output.mkdir(parents=True, exist_ok=False)

    records_path = resolved_output / "records.jsonl"
    records = build_records(questions)
    records_path.write_text(
        "\n".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records
        )
        + "\n",
        encoding="utf-8",
    )
    accepted_path = resolved_output / "accepted.json"
    accepted = build_accepted(questions, review, proposal)
    write_json(accepted_path, accepted)
    confirmation_path = resolved_output / "confirmation.json"
    write_json(confirmation_path, build_confirmation(accepted, proposal_path, records_path))
    readme_path = resolved_output / "README.md"
    readme_path.write_text(
        "# Semantic Facet Blind v2 Confirmed\n\n"
        "状态：user_confirmed。该数据集是基于已有开发错误设计的定向盲评候选集，独立测试集标志保持 false。\n\n"
        "records.jsonl 是用于 adapter 对齐的 PARTIAL fixture；accepted.json 保存确认的 Facet、binding 和原文 Span；confirmation.json 保存确认范围与 provenance。原始待审阅文件仍保存在 `docs/gate_v3/semantic-facet-blind-v2-draft/`。\n\n"
        "本包包含 18 条 query、17 个 Facet 实例和两条空 Facet 真值。当前不包含模型推理、检索、运行时接入或语义准确率。\n",
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
            {"path": question_path.relative_to(root).as_posix(), "sha256": sha256(question_path)},
            {"path": review_path.relative_to(root).as_posix(), "sha256": sha256(review_path)},
            {"path": PROPOSAL_PATH.as_posix(), "sha256": sha256(proposal_path)},
            {"path": PROMPT_PATH.as_posix(), "sha256": sha256(prompt_path)},
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--output", type=Path, default=Path("docs/gate_v3/semantic-facet-blind-v2-confirmed")
    )
    args = parser.parse_args()
    output = freeze_dataset(args.project_root, args.output)
    print(json.dumps({"status": "passed", "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
