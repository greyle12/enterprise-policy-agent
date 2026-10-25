"""Two independent offline captures of the fixed answer-prompt experiment; no live switch."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from app.evaluation.agent_capture import capture_answer_prompt
from scripts.run_agent_workflow_capture import run as capture
from scripts.run_capture_comparison import run as compare


async def run(output):
    enabled = os.environ.get("AGENT_TRACING_ENABLED")
    os.environ["AGENT_TRACING_ENABLED"] = "false"
    try:
        output.mkdir(parents=True, exist_ok=False)
        summaries = {}
        for variant in ("baseline", "completeness-v1"):
            folder = output / variant
            summaries[variant] = await capture(folder, answer_prompt_variant=variant)
            prompt = capture_answer_prompt(variant)
            (folder / "answer-system-prompt.txt").write_text(prompt, "utf-8", newline="\n")
            report = json.loads((folder / "capture-report.json").read_text("utf-8"))
            assert (
                hashlib.sha256(prompt.encode()).hexdigest()
                == report["manifest"]["answer_prompt_sha256"]
            )
            # Deliberately invalid until model, budget and authorized data scope are supplied.
            pending = {
                "model": None,
                "base_url": "https://api.deepseek.com",
                "api_key_env": "EVAL_API_KEY",
                "budget": None,
                "approved_data_scope": None,
                "answer_prompt_variant": variant,
                "dataset_sha256": report["manifest"]["datasets"],
                "corpus_sha256": report["manifest"]["corpus_sha256"],
                "source_sha256": report["manifest"]["source_sha256"],
            }
            (folder / "live-config.pending.json").write_text(
                json.dumps(pending, indent=2) + "\n", "utf-8"
            )
        comparison = await compare(
            output / "baseline/capture-report.json",
            output / "completeness-v1/capture-report.json",
            output / "comparison",
        )
        summary = {
            "experiment": "same_model_answer_prompt_completeness_v1",
            "mode": "offline",
            "captures": summaries,
            "paired_answers": comparison["paired_answers"],
            "mock_judge_calls": comparison["mock_judge_calls"],
            "real_ab_input_ready": comparison["real_ab_input_ready"],
            "real_ab_blockers": comparison["real_ab_blockers"],
            "engineering_gate_passed": all(s["workflow_gate_passed"] for s in summaries.values())
            and comparison["engineering_gate_passed"],
            "live_model_calls": 0,
            "quality_winner": None,
            "human_review_decisions_added": 0,
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
        (output / "delivery-manifest.json").write_text(
            json.dumps(
                {
                    p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(output.rglob("*"))
                    if p.is_file()
                },
                indent=2,
            )
            + "\n",
            "utf-8",
        )
        return summary
    finally:
        if enabled is None:
            os.environ.pop("AGENT_TRACING_ENABLED", None)
        else:
            os.environ["AGENT_TRACING_ENABLED"] = enabled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    summary = asyncio.run(run(args.output_dir))
    print(json.dumps(summary, indent=2))
    return 0 if summary["engineering_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
