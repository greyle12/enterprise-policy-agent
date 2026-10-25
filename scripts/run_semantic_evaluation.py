"""Explicit offline/live Judge and pairwise runner; live budgets are mandatory."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

from app.evaluation.judge import (
    JudgeBudget,
    JudgeInput,
    LiveJudgeProvider,
    MockJudgeProvider,
    SemanticJudge,
    calibration,
)
from app.evaluation.judge import SYSTEM_PROMPT, PROMPT_VERSION, RUBRICS
from app.evaluation.pairwise import compare_pair, summarize_pairs


async def run(args):
    rows = [json.loads(s) for s in args.input.read_text(encoding="utf-8").splitlines() if s.strip()]
    if not rows or len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("empty input or duplicate case IDs")
    budget = JudgeBudget(
        token_limit=args.token_budget,
        cost_limit=args.cost_budget,
        max_output_tokens=args.max_output_tokens,
        max_usd_per_million=args.max_usd_per_million,
        timeout_seconds=args.timeout,
        max_calls=args.max_calls,
    )
    provider = (
        LiveJudgeProvider(
            model=args.model,
            api_key=os.environ["EVAL_API_KEY"],
            base_url=os.environ["EVAL_BASE_URL"],
            timeout=args.timeout,
        )
        if args.mode == "live"
        else MockJudgeProvider(
            json.dumps(
                dict(
                    verdict="UNKNOWN",
                    rationale="offline plumbing fixture; no semantic judgment",
                    evidence_ids=[],
                    preference="Unknown",
                )
            )
        )
    )
    judge = SemanticJudge(provider, budget)
    results = []
    try:
        for repetition in range(args.repetitions):
            for row in rows:
                if args.kind == "semantic":
                    item = JudgeInput.model_validate(row)
                    result = (await judge.evaluate(item)).model_dump(mode="json")
                    result.update(case_id=item.case_id, repetition=repetition)
                else:
                    result = await compare_pair(judge, **row, seed=args.seed + repetition)
                    result["repetition"] = repetition
                results.append(result)
    finally:
        if args.mode == "live":
            await provider.close()
    report = {
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(
            (SYSTEM_PROMPT + json.dumps(RUBRICS, sort_keys=True)).encode()
        ).hexdigest(),
        "evidence_sha256": hashlib.sha256(
            json.dumps([row["evidence"] for row in rows], sort_keys=True).encode()
        ).hexdigest(),
        "mode": args.mode,
        "kind": args.kind,
        "model_version": provider.model_version,
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "budget": budget.model_dump(),
        "reserved_tokens": judge.reserved_tokens,
        "calls": judge.calls,
        "repetitions": args.repetitions,
        "results": results,
        "calibration": calibration([]),
        "calibration_status": "pending_human_review",
    }
    if args.calibration_labels:
        if args.kind != "semantic":
            raise ValueError("binary calibration is only defined for semantic results")
        labels = [
            json.loads(s)
            for s in args.calibration_labels.read_text(encoding="utf-8").splitlines()
            if s.strip()
        ]
        keys = [(r["case_id"], r["repetition"]) for r in labels]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate calibration labels")
        observed = {(r["case_id"], r["repetition"]): r for r in results}
        if not set(keys) <= set(observed):
            raise ValueError("calibration label references unknown case")
        report["calibration"] = calibration(
            [
                {**label, "judge": observed[key]["decision"]["verdict"]}
                for key, label in zip(keys, labels, strict=True)
            ]
        )
        report["calibration_status"] = "human_labels_supplied_not_certified"
    if args.kind == "pairwise":
        # Repetitions are not independent samples: report each repetition separately.
        report["summaries"] = [
            summarize_pairs([r for r in results if r["repetition"] == i], seed=args.seed)
            for i in range(args.repetitions)
        ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "judge-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "judge-report.md").write_text(
        f"# Judge evaluation\n\nMode: {args.mode}; calls: {judge.calls}; calibration: pending human review.\n"
        "\nSemantic results are not a deployment gate. See JSON for UNKNOWN/error/usage details.\n",
        encoding="utf-8",
    )
    (args.output_dir / "calibration-pending.jsonl").write_text(
        "".join(
            json.dumps(
                dict(
                    case_id=r["case_id"],
                    repetition=r["repetition"],
                    human=None,
                    reviewer=None,
                    review_status="pending_review",
                    review_mode="assistant_assisted_human_review",
                )
            )
            + "\n"
            for r in results
        ),
        encoding="utf-8",
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--kind", choices=["semantic", "pairwise"], default="semantic")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--token-budget", type=int, required=True)
    parser.add_argument("--cost-budget", type=float, required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    parser.add_argument("--max-usd-per-million", type=float, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--repetitions", type=int, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--calibration-labels", type=Path)
    args = parser.parse_args()
    if args.repetitions < 1 or (args.mode == "live" and not args.model):
        parser.error("positive repetitions and explicit live model required")
    report = asyncio.run(run(args))
    print(json.dumps({k: v for k, v in report.items() if k not in {"results", "budget"}}, indent=2))
    return (
        1
        if any(
            r.get("status") in {"invalid_output", "provider_error", "budget_exhausted"}
            or r.get("judge_failures", 0)
            for r in report["results"]
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
