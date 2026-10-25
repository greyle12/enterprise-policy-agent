"""Prepare captured A/B pairs and verify the blind/swap pipeline using offline Mock only."""

import argparse
import asyncio
import json
from argparse import Namespace
from pathlib import Path

from scripts.prepare_capture_comparison import run as prepare
from scripts.run_semantic_evaluation import run as evaluate


async def run(
    baseline, candidate, output, allowed_changes=(), baseline_review=None, candidate_review=None
):
    summary = prepare(
        baseline, candidate, output, allowed_changes, baseline_review, candidate_review
    )
    if summary["paired_answers"]:
        judge = await evaluate(
            Namespace(
                input=output / "pairwise-inputs.jsonl",
                output_dir=output / "offline-pairwise",
                mode="offline",
                kind="pairwise",
                model=None,
                calibration_labels=None,
                token_budget=200000,
                cost_budget=1,
                max_output_tokens=500,
                max_usd_per_million=1,
                timeout=5,
                max_calls=100,
                repetitions=1,
                seed=17,
            )
        )
        errors = sum(r.get("judge_failures", 0) for r in judge["results"])
        summary.update(mock_judge_calls=judge["calls"], pairwise_summaries=judge["summaries"])
    else:
        errors = 0
        summary.update(
            mock_judge_calls=0, pairwise_summaries=[], pairwise_status="skipped_no_eligible_pairs"
        )
    summary["engineering_gate_passed"] = not summary["regressions"] and errors == 0
    summary["comparison_note"] = (
        "offline Mock only; no candidate quality ranking or model improvement established"
    )
    (output / "delivery-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    (output / "delivery-summary.md").write_text(
        f"# Capture comparison\n\nKind: {summary['comparison_kind']}; cases: {summary['case_count']}; paired answers: {summary['paired_answers']}.\n\n"
        f"Regressions: {summary['regressions']}. Mock calls: {summary['mock_judge_calls']}. New live calls: 0.\n\n"
        "This verifies comparison plumbing. No quality winner is selected.\n",
        "utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-source-change", action="append", default=[])
    parser.add_argument("--baseline-review", type=Path)
    parser.add_argument("--candidate-review", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(
        run(
            args.baseline,
            args.candidate,
            args.output_dir,
            args.allow_source_change,
            args.baseline_review,
            args.candidate_review,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["engineering_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
