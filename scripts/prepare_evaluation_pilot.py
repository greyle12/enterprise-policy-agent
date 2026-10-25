"""Snapshot E6 evidence and prepare E7 review material without network calls."""

import argparse
import hashlib
import json
import platform
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.evaluation.judge import JudgeInput
from app.evaluation.workflow_evaluation import load_workflow_cases

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path("artifacts/evaluation-e2-e5-final")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()


def verify_delivery(root):
    manifest = json.loads((root / EVIDENCE / "delivery-manifest.json").read_text("utf-8"))
    checks = {
        name: {"expected": digest, "actual": sha(root / name) if (root / name).is_file() else None}
        for name, digest in manifest.items()
    }
    for check in checks.values():
        check["matches"] = check["actual"] == check["expected"]
    return checks


def prepare(output):
    # Reject an existing directory before writing any artifact.
    if output.exists():
        raise FileExistsError(output)
    checks = verify_delivery(ROOT)
    if not all(c["matches"] for c in checks.values()):
        raise ValueError("delivery manifest drift; inspect before preparing pilot")
    datasets = [
        ROOT / f"tests/evaluation/{name}.jsonl"
        for name in ("workflow_cases", "workflow_challenge_cases", "workflow_safety_cases")
    ]
    workflow = []
    for dataset in datasets:
        for case in load_workflow_cases(dataset):
            workflow.append(
                dict(
                    case_id=case.case_id,
                    scenario=case.scenario,
                    source=dataset.relative_to(ROOT).as_posix(),
                    source_sha256=sha(dataset),
                    split=case.split,
                    turns=len(case.turns),
                    status="pending_live_agent_capture",
                    output_sha256=None,
                    reviewer_decision=None,
                    review_mode="assistant_assisted_human_review",
                )
            )
    smoke_path = ROOT / "tests/evaluation/semantic_smoke.jsonl"
    smoke = [
        JudgeInput.model_validate_json(line)
        for line in smoke_path.read_text("utf-8").splitlines()
        if line.strip()
    ]
    junit_path = ROOT / EVIDENCE / "acceptance.xml"
    junit = ET.parse(junit_path).getroot()
    suites = [junit] if junit.tag == "testsuite" else list(junit.iter("testsuite"))
    inventory = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "python": platform.python_version(),
        "tracked_changes": git("diff", "--name-status", "HEAD").splitlines(),
        "untracked_source_paths": git(
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "app",
            "scripts",
            "tests",
            "docs",
            ".github",
        ).splitlines(),
        "scope": "tracked changes plus untracked source paths; no credentials or runtime directories",
        "delivery_file_checks": checks,
        "recorded_acceptance": {
            "source": junit_path.relative_to(ROOT).as_posix(),
            "sha256": sha(junit_path),
            **{
                key: sum(int(s.get(key, 0)) for s in suites)
                for key in ("tests", "failures", "errors", "skipped")
            },
            "rerun_this_step": False,
        },
        "live_calls": 0,
        "human_reviews": 0,
    }
    inventory["package_versions"] = {}
    for package in ("pydantic", "langgraph", "openai", "psycopg", "langfuse"):
        try:
            inventory["package_versions"][package] = version(package)
        except PackageNotFoundError:
            inventory["package_versions"][package] = None
    # Snapshot the current source bytes; HEAD alone does not identify a dirty checkout.
    source_paths = set(json.loads((ROOT / EVIDENCE / "delivery-manifest.json").read_text("utf-8")))
    source_paths.update(
        p.relative_to(ROOT).as_posix()
        for folder in ("app", "scripts", "tests")
        for p in (ROOT / folder).rglob("*.py")
        if "__pycache__" not in p.parts
    )
    source_paths.add("pyproject.toml")
    source_paths.update(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "data/policies").rglob("*.md")
    )
    source_hashes = {p: sha(ROOT / p) for p in sorted(source_paths)}
    inventory["source_fingerprint"] = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True).encode()
    ).hexdigest()
    config = {
        "status": "pending_configuration",
        "tested_agent_model": None,
        "judge_model": None,
        "endpoint": None,
        "allowed_data_scope": None,
        "token_budget": None,
        "cost_budget_usd": None,
        "max_usd_per_million": None,
        "max_calls": None,
        "timeout_seconds": None,
        "max_output_tokens": None,
        "repetitions": 1,
        "credentials": "process environment only; never put API keys in this file",
        "judge_smoke_input": "tests/evaluation/semantic_smoke.jsonl",
        "judge_smoke_input_sha256": sha(smoke_path),
        "scope_note": "Judge smoke uses authored answers; live Agent capture is a separate pending task",
    }
    review = [
        dict(
            case_id=row.case_id,
            repetition=0,
            rubric=row.rubric,
            query=row.query,
            answer=row.answer,
            reference=row.reference,
            evidence=row.evidence,
            input_sha256=sha(smoke_path),
            answer_origin="authored_synthetic_fixture",
            human=None,
            reviewer=None,
            rationale=None,
            review_status="pending_review",
            review_mode="assistant_assisted_human_review",
            reviewer_decision=None,
        )
        for row in smoke
    ]
    output.mkdir(parents=True)
    for name, data in {
        "baseline.json": inventory,
        "source-hashes.json": source_hashes,
        "live-config.template.json": config,
        "workflow-pilot-plan.json": workflow,
    }.items():
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (output / "judge-blind-review.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in review), "utf-8"
    )
    return {
        "delivery_files_verified": len(checks),
        "workflow_cases": len(workflow),
        "review_rows": len(review),
        "live_ready": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output_dir), indent=2))


if __name__ == "__main__":
    main()
