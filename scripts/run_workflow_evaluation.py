"""Offline state-based evaluation: no live models or external approval services."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from app.evaluation.failure_pipeline import write_pending_cards
from app.evaluation.workflow_evaluation import load_workflow_cases, run_sqlite_cases

ROOT = Path(__file__).resolve().parents[1]


async def run(dataset, output):
    cases = load_workflow_cases(dataset)
    with tempfile.TemporaryDirectory(prefix="workflow-evaluation-") as directory:
        results = await run_sqlite_cases(cases, directory, ROOT / "data/policies")
    manifest = {
        "source_fingerprint": hashlib.sha256(
            b"".join(
                p.relative_to(ROOT).as_posix().encode() + p.read_bytes()
                for p in sorted((ROOT / "app").rglob("*.py"))
            )
        ).hexdigest(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "dataset_version": sorted({case.dataset_version for case in cases}),
        "model_version": "offline_keyword_v1",
        "prompt_version": "not_used",
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain", "-uno"], cwd=ROOT, text=True)
        ),
        "corpus_fingerprint": hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted((ROOT / "data/policies").rglob("*.md")))
        ).hexdigest(),
    }
    passed = sum(r["passed"] for r in results)
    assertions = [a for r in results for a in r["assertions"]]
    metrics = {}
    for metric, suffixes in {
        "intent_accuracy": (".intent",),
        "tool_selection_accuracy": (".tool_selection",),
        "tool_argument_accuracy": (".tool_arguments",),
        "trajectory_validity": (".trajectory",),
        "final_state_correctness": (
            ".persisted_phase",
            ".submission_persisted",
            ".draft_lifecycle",
            ".draft_submitted_flag",
            ".submission_identity",
        ),
        "human_confirmation_compliance": (".confirmation_compliance",),
        "abstention_correctness": (".abstention",),
        "citation_correctness": (".citation_sources",),
        "recovery_success_rate": (".recovery", ".reconciled"),
    }.items():
        selected = [a for a in assertions if a["name"].endswith(suffixes)]
        metrics[metric] = {
            "n": len(selected),
            "rate": sum(a["passed"] for a in selected) / len(selected) if selected else None,
        }
    metrics["task_success_rate"] = {"n": len(results), "rate": passed / len(results)}
    completed = [
        all(
            a["passed"]
            for a in r["assertions"]
            if a["name"].endswith((".status", ".persisted_phase", ".error"))
        )
        for r in results
    ]
    metrics["workflow_completion_rate"] = {
        "n": len(results),
        "rate": sum(completed) / len(completed),
    }
    duplicate = [a for a in assertions if a["name"].endswith(".duplicate_side_effect")]
    metrics["duplicate_side_effect_rate"] = {
        "n": len(duplicate),
        "rate": sum(not a["passed"] for a in duplicate) / len(duplicate) if duplicate else None,
    }
    acl = [a for a in assertions if a["name"].endswith(".authorization_violation")]
    metrics["authorization_violation_rate"] = {
        "n": len(acl),
        "rate": sum(a["actual"] is True for a in acl) / len(acl) if acl else None,
    }
    report = {
        "manifest": manifest,
        "evidence_mode": "deterministic_integration",
        "total": len(results),
        "passed": passed,
        "quality_gate_passed": passed == len(results),
        "results": results,
        "metrics": metrics,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "workflow-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "workflow-report.md").write_text(
        f"# Workflow evaluation\n\nDeterministic integration: {passed}/{len(results)} passed.\n\n"
        + "\n".join(f"- {r['case_id']}: {'PASS' if r['passed'] else 'FAIL'}" for r in results),
        encoding="utf-8",
    )
    write_pending_cards(results, output / "pending-review.jsonl")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "tests/evaluation/workflow_cases.jsonl"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/workflow-evaluation")
    parser.add_argument("--publish-langfuse", action="store_true")
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    report = asyncio.run(run(args.dataset, args.output_dir))
    if args.baseline:
        from app.evaluation.failure_pipeline import regression_summary

        comparison = regression_summary(baseline, report)
        (args.output_dir / "regression-summary.json").write_text(
            json.dumps(comparison, indent=2), encoding="utf-8"
        )
    if args.publish_langfuse:
        from app.evaluation.experiment_tracking import publish_workflow_experiment
        from app.observability.tracing import get_backend, shutdown_tracing

        backend = get_backend()
        outcome = (
            publish_workflow_experiment(report, backend.client)
            if backend
            else {"status": "disabled_or_unavailable", "server_receipt_verified": False}
        )
        (args.output_dir / "langfuse-export.json").write_text(json.dumps(outcome), encoding="utf-8")
        shutdown_tracing()
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    return 0 if report["quality_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
