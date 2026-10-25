"""Promote only explicitly reviewed, source-bound workflow failures to regression JSONL."""

import argparse
import hashlib
import json
from pathlib import Path

from app.evaluation.failure_pipeline import approve_regression, failure_cards
from app.evaluation.workflow_evaluation import WorkflowCase, load_workflow_cases


def promote(dataset, report_path, decisions_path, output):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if hashlib.sha256(dataset.read_bytes()).hexdigest() != report["manifest"]["dataset_sha256"]:
        raise ValueError("dataset fingerprint mismatch")
    cases = {c.case_id: c for c in load_workflow_cases(dataset)}
    cards = {c["case_id"]: c for c in failure_cards(report["results"])}
    decisions = [
        json.loads(s) for s in decisions_path.read_text(encoding="utf-8").splitlines() if s.strip()
    ]
    if not decisions or len({d["case_id"] for d in decisions}) != len(decisions):
        raise ValueError("empty or duplicate review decisions")
    rows, receipts = [], []
    for decision in decisions:
        card = cards[decision["case_id"]]
        if card["source_split"] != cases[decision["case_id"]].split:
            raise ValueError("case split provenance mismatch")
        if decision["source_sha256"] != card["source_sha256"]:
            raise ValueError("reviewed execution fingerprint mismatch")
        if decision["decision"] == "reject":
            continue
        receipt = approve_regression(
            card,
            reviewer=decision["reviewer"],
            decision=decision["decision"],
            rationale=decision["rationale"],
        )
        case = cases[decision["case_id"]].model_dump(mode="json")
        case.update(split="regression", dataset_version="workflow-regression-v1")
        rows.append(WorkflowCase.model_validate(case).model_dump_json())
        receipts.append(receipt)
    # Never overwrite either the source dataset or an earlier approved dataset.
    if not rows:
        raise ValueError("no approved regression cases")
    receipt_path = output.with_suffix(".review.json")
    if output.exists() or receipt_path.exists():
        raise FileExistsError("regression output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(rows) + "\n")
    with receipt_path.open("x", encoding="utf-8") as stream:
        json.dump(receipts, stream, ensure_ascii=False, indent=2)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("dataset", "report", "decisions", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args()
    print(promote(args.dataset, args.report, args.decisions, args.output))


if __name__ == "__main__":
    main()
