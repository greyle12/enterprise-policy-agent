"""One local command: full workflow capture, persisted-state checks and semantic plumbing."""

import argparse
import asyncio
import json
import xml.etree.ElementTree as ET
from argparse import Namespace
from pathlib import Path

from scripts.run_agent_workflow_capture import (
    run as capture_run,
    input_manifest,
    corpus_hash,
    source_manifest,
)
from scripts.run_semantic_evaluation import run as semantic_run


async def run(output):
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    workflow = await capture_run(output / "workflow")
    if workflow["semantic_faithfulness_inputs"] == 0:
        semantic = {"calls": 0, "results": [], "status": "skipped_no_eligible_answers"}
        (output / "semantic").mkdir()
        (output / "semantic/judge-report.json").write_text(
            json.dumps(semantic, indent=2) + "\n", "utf-8"
        )
    else:
        semantic = await semantic_run(
            Namespace(
                input=output / "workflow/semantic-inputs.jsonl",
                output_dir=output / "semantic",
                mode="offline",
                kind="semantic",
                model=None,
                calibration_labels=None,
                token_budget=100000,
                cost_budget=1,
                max_output_tokens=500,
                max_usd_per_million=1,
                timeout=5,
                max_calls=20,
                repetitions=1,
                seed=0,
            )
        )
    semantic_ok = all(r["status"] == "ok" for r in semantic["results"])
    summary = {
        **workflow,
        "semantic_mock_calls": semantic["calls"],
        "semantic_verdicts": [r["decision"]["verdict"] for r in semantic["results"]],
        "engineering_gate_passed": workflow["workflow_gate_passed"] and semantic_ok,
        "quality_conclusion": None,
        "external_publication": False,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    report = json.loads((output / "workflow/capture-report.json").read_text("utf-8"))
    suite = ET.Element("testsuite", name="offline_agent_evaluation_batch")
    failures = 0
    for row in report["workflow_results"] + semantic["results"]:
        passed = row.get("passed", row.get("status") == "ok")
        case = ET.SubElement(
            suite, "testcase", name=row["case_id"], classname="offline_integration"
        )
        if not passed:
            failures += 1
            ET.SubElement(case, "failure", message="workflow assertion or Judge transport failed")
    suite.set("tests", str(len(suite)))
    suite.set("failures", str(failures))
    suite.set("errors", "0")
    suite.set("skipped", "0")
    ET.ElementTree(suite).write(output / "batch-junit.xml", encoding="utf-8", xml_declaration=True)
    # Missing values intentionally keep this template unusable for live calls until configured.
    template = {
        "model": None,
        "base_url": None,
        "api_key_env": "EVAL_API_KEY",
        "budget": None,
        "dataset_sha256": input_manifest(),
        "corpus_sha256": corpus_hash(),
        "source_sha256": source_manifest(),
        "approved_data_scope": None,
    }
    (output / "live-config.pending.json").write_text(json.dumps(template, indent=2) + "\n", "utf-8")
    (output / "summary.md").write_text(
        f"# Local Agent evaluation batch\n\nWorkflow: {workflow['workflow_passed']}/{workflow['cases']}, "
        f"turns: {workflow['turns']}. Semantic Mock calls: {semantic['calls']}; UNKNOWN is not a quality pass.\n\n"
        "Live calls: 0. Human reviews: 0. Real model quality remains unverified.\n",
        "utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = asyncio.run(run(args.output_dir))
    print(json.dumps(summary, indent=2))
    return 0 if summary["engineering_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
