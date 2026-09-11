"""Run deterministic evaluator boundary fixtures against the frozen sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from scripts.evaluate_semantic_request_facets import evaluate_predictions, load_prediction_dataset
from scripts.semantic_request_facets_adapter import load_semantic_request_facet_bundle


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prediction_rows(bundle):
    return [
        {
            "schema_version": "1.0",
            "case_id": case.case_id,
            "predicted_facets": [
                {"facet": facet.facet, "binding": facet.binding} for facet in case.accepted_facets
            ],
        }
        for case in bundle.cases
    ]


def write_predictions(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def run_fixture(
    bundle, output: Path, fixture_id: str, rows: list[dict[str, object]]
) -> dict[str, object]:
    prediction_path = output / f"{fixture_id}.predictions.jsonl"
    write_predictions(prediction_path, rows)
    evaluated = evaluate_predictions(bundle, load_prediction_dataset(prediction_path))
    report = {
        "schema_version": "1.0",
        "fixture_id": fixture_id,
        "model_inference": False,
        "retrieval_executed": False,
        "parser_runtime_integration": False,
        "prediction_sha256": digest(prediction_path),
        "metrics": evaluated["metrics"],
        "missing_prediction_case_ids": evaluated["missing_prediction_case_ids"],
        "cases": evaluated["cases"],
    }
    (output / f"{fixture_id}.evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    bundle_root = root / "docs/gate_v3/semantic-facet-blind-v2-confirmed"
    bundle = load_semantic_request_facet_bundle(
        bundle_root / "records.jsonl",
        bundle_root / "accepted.json",
        bundle_root / "confirmation.json",
        manifest_path=bundle_root / "manifest.json",
        project_root=root,
    )
    output = (root / args.output if not args.output.is_absolute() else args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    complete = prediction_rows(bundle)
    reports = [run_fixture(bundle, output, "complete", complete)]

    extra = json.loads(json.dumps(complete))
    extra[8]["predicted_facets"] = [{"facet": "DEADLINE", "binding": "BOUND"}]
    reports.append(run_fixture(bundle, output, "extra-facet", extra))

    missing = [row for row in complete if row["case_id"] != "SFB-012"]
    reports.append(run_fixture(bundle, output, "missing-prediction", missing))

    summary = {
        "schema_version": "1.0",
        "status": "passed",
        "scope": "evaluator_boundary_fixtures",
        "dataset_version": bundle.dataset_version,
        "source_records_sha256": bundle.source_records_sha256,
        "source_proposal_sha256": bundle.source_proposal_sha256,
        "case_count": len(bundle.cases),
        "accepted_facet_instance_count": bundle.accepted_facet_instance_count,
        "code_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "script_sha256": digest(Path(__file__)),
        "model_inference": False,
        "reports": [
            {
                "fixture_id": report["fixture_id"],
                "prediction_sha256": report["prediction_sha256"],
                "metrics": report["metrics"],
                "missing_prediction_case_ids": report["missing_prediction_case_ids"],
            }
            for report in reports
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
