"""One offline E2-E5 execution: workflow, semantic plumbing, bound pairwise and review cards."""

import argparse
import asyncio
import json
import xml.etree.ElementTree as ET
from argparse import Namespace
from pathlib import Path

from scripts.run_workflow_evaluation import ROOT, run as workflow_run
from scripts.run_semantic_evaluation import run as semantic_run


async def run(output):
    reports = []
    for name in ("workflow_cases", "workflow_challenge_cases", "workflow_safety_cases"):
        reports.append(await workflow_run(ROOT / f"tests/evaluation/{name}.jsonl", output / name))
    # An explicitly injected expectation fault demonstrates mining without touching source labels.
    fault_case = json.loads(
        (ROOT / "tests/evaluation/workflow_cases.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    fault_case["tags"] = ["injected_expectation_fault", "not_ground_truth"]
    fault_case["turns"][-1]["submissions"] = 2
    fault_dataset = output / "injected-fault-demo.jsonl"
    fault_dataset.write_text(json.dumps(fault_case, ensure_ascii=False) + "\n", encoding="utf-8")
    fault_report = await workflow_run(fault_dataset, output / "injected-fault-demo")
    fault_detected = not fault_report["quality_gate_passed"] and bool(
        (output / "injected-fault-demo/pending-review.jsonl").read_text(encoding="utf-8").strip()
    )
    options = dict(
        mode="offline",
        token_budget=50000,
        cost_budget=1,
        max_output_tokens=500,
        max_usd_per_million=1,
        timeout=10,
        max_calls=10,
        repetitions=1,
        seed=0,
        model=None,
        calibration_labels=None,
    )
    semantic = await semantic_run(
        Namespace(
            **options,
            kind="semantic",
            input=ROOT / "tests/evaluation/semantic_smoke.jsonl",
            output_dir=output / "semantic",
        )
    )
    result = reports[0]["results"][0]
    manifest = reports[0]["manifest"]
    candidate = {
        "answer": "合成文本比较占位，不代表真实模型回答。",
        "workflow_result": result,
        "manifest": {
            "dataset_sha256": manifest["dataset_sha256"],
            "corpus_sha256": manifest["corpus_fingerprint"],
            "principal_sha256": "synthetic_employee",
            "tool_fixture_sha256": manifest["source_fingerprint"],
        },
    }
    pair_input = output / "bound-pairwise.jsonl"
    pair_input.write_text(
        json.dumps(
            {
                "case_id": result["case_id"],
                "query": "比较两份合成申请说明。",
                "evidence": {"synthetic": "合成申请必须先确认后提交。"},
                "candidate_a": candidate,
                "candidate_b": candidate,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    paired = await semantic_run(
        Namespace(**options, kind="pairwise", input=pair_input, output_dir=output / "pairwise")
    )
    summary = {
        "evidence_mode": "deterministic_integration_and_offline_mock",
        "workflow_total": sum(r["total"] for r in reports),
        "workflow_passed": sum(r["passed"] for r in reports),
        "quality_gate_passed": all(r["quality_gate_passed"] for r in reports),
        "semantic_statuses": [r["status"] for r in semantic["results"]],
        "pairwise": paired["summaries"],
        "human_calibration_samples": 0,
        "live_model_calls": 0,
        "external_publication": False,
        "pairwise_eligible": all(r["status"] == "evaluated" for r in paired["results"]),
        "pairwise_errors": sum(r.get("judge_failures", 0) for r in paired["results"]),
        "injected_fault_detected": fault_detected,
    }
    junit = ET.Element("testsuite", name="evaluation-delivery")
    for report in reports:
        for case in report["results"]:
            node = ET.SubElement(
                junit, "testcase", name=case["case_id"], classname="deterministic_integration"
            )
            if not case["passed"]:
                ET.SubElement(
                    node, "failure", message="workflow assertions failed"
                ).text = json.dumps([a for a in case["assertions"] if not a["passed"]])
    for kind, report in (("semantic", semantic), ("pairwise", paired)):
        for result in report["results"]:
            node = ET.SubElement(
                junit, "testcase", name=kind + ":" + result["case_id"], classname="offline_mock"
            )
            if result["status"] not in {"ok", "evaluated"} or result.get("judge_failures", 0):
                ET.SubElement(node, "failure", message="offline execution contract failed")
    junit.set("tests", str(len(junit)))
    node = ET.SubElement(
        junit, "testcase", name="injected_fault_detection", classname="fault_injection"
    )
    if not fault_detected:
        ET.SubElement(node, "failure", message="injected failure was not mined")
    junit.set("tests", str(len(junit)))
    junit.set("failures", str(sum(node.find("failure") is not None for node in junit)))
    ET.ElementTree(junit).write(
        output / "delivery-junit.xml", encoding="utf-8", xml_declaration=True
    )
    (output / "delivery-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output / "delivery-summary.md").write_text(
        f"# E2-E5 offline delivery\n\nWorkflow: {summary['workflow_passed']}/{summary['workflow_total']}.\n\n"
        "Semantic/pairwise: offline Mock, UNKNOWN expected. Human calibration: pending.\n",
        encoding="utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/evaluation-delivery")
    args = parser.parse_args()
    summary = asyncio.run(run(args.output_dir))
    print(json.dumps(summary, indent=2))
    return (
        0
        if (
            summary["quality_gate_passed"]
            and summary["pairwise_eligible"]
            and not summary["pairwise_errors"]
            and summary["injected_fault_detected"]
            and all(s == "ok" for s in summary["semantic_statuses"])
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
