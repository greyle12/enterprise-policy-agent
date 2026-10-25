"""Capture all workflow datasets, evaluate persisted state, export semantic inputs locally."""

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.evaluation.agent_capture import (
    AnswerPromptVariant,
    BudgetedCaptureClient,
    capture_answer_prompt,
    capture_workflow_cases,
)
from app.evaluation.capture_provider import DeepSeekCaptureProvider
from app.evaluation.failure_pipeline import write_pending_cards
from app.evaluation.judge import JudgeBudget, JudgeInput
from app.evaluation.models import _StrictModel
from app.evaluation.workflow_evaluation import load_workflow_cases
from scripts.capture_agent_pilot import ScriptedCaptureProvider

ROOT = Path(__file__).resolve().parents[1]
DATASETS = [
    ROOT / f"tests/evaluation/{n}.jsonl"
    for n in ("workflow_cases", "workflow_challenge_cases", "workflow_safety_cases")
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LiveCaptureConfig(_StrictModel):
    model: str = Field(min_length=1)
    base_url: str
    api_key_env: Literal["EVAL_API_KEY"] = "EVAL_API_KEY"
    budget: JudgeBudget
    dataset_sha256: dict[str, str]
    corpus_sha256: str
    source_sha256: dict[str, str]
    approved_data_scope: Literal["workflow_development_and_synthetic_fixtures"]
    answer_prompt_variant: AnswerPromptVariant = "baseline"


def input_manifest():
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in DATASETS}


def corpus_hash():
    return hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted((ROOT / "data/policies").rglob("*.md")))
    ).hexdigest()


def source_manifest():
    paths = sorted((ROOT / "app").rglob("*.py")) + [
        Path(__file__),
        ROOT / "scripts/capture_agent_pilot.py",
    ]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}


def make_provider(mode, config_path, answer_prompt_variant="baseline"):
    if mode == "offline":
        if config_path:
            raise ValueError("live config is not used by offline runs")
        return ScriptedCaptureProvider(), JudgeBudget(
            token_limit=1000000,
            cost_limit=10,
            max_output_tokens=1000,
            max_usd_per_million=1,
            timeout_seconds=5,
            max_calls=100,
        )
    if mode != "live" or config_path is None:
        raise ValueError("explicit live config required")
    config = LiveCaptureConfig.model_validate_json(config_path.read_text("utf-8"))
    if (
        config.dataset_sha256 != input_manifest()
        or config.corpus_sha256 != corpus_hash()
        or config.source_sha256 != source_manifest()
        or config.answer_prompt_variant != answer_prompt_variant
    ):
        raise ValueError("live input fingerprint mismatch")
    key = os.environ.get(config.api_key_env)
    if not key:
        raise ValueError("dedicated EVAL_API_KEY process environment required")
    return DeepSeekCaptureProvider(
        model=config.model,
        api_key=key,
        base_url=config.base_url,
        timeout_seconds=config.budget.timeout_seconds,
    ), config.budget


def export_semantic(captures):
    """Export only successful evidence-backed policy answers; never invent references."""
    rows, bindings, skipped = [], [], []
    for case in captures:
        for turn in case["turns"]:
            result = turn["result"]
            reasons = []
            if (
                not result
                or result["status"] != "completed"
                or result["classification"]["intent"] != "policy_query"
            ):
                reasons.append("not_completed_policy_answer")
            evidence = {}
            evidence_records = {}
            for call in turn["provider_records"]:
                if call.get("status") != "ok":
                    continue
                for message in call["messages"]:
                    text = message["content"]
                    if "\n<policy_evidence_json>" in text:
                        payload = text.split("\n<policy_evidence_json>", 1)[1].removesuffix(
                            "</policy_evidence_json>"
                        )
                        for entry in json.loads(payload):
                            evidence[entry["source_id"]] = entry["content"]
                            evidence_records[entry["source_id"]] = entry
            if not evidence:
                reasons.append("no_authorized_answer_evidence")
            if reasons:
                skipped.append(
                    {"case_id": case["case_id"], "turn": turn["turn"], "reasons": reasons}
                )
                continue
            item_id = f"{case['case_id']}-T{turn['turn']}-faithfulness"
            item = JudgeInput(
                case_id=item_id,
                rubric="faithfulness",
                query=turn["query"],
                answer=result["reply"],
                evidence=evidence,
            )
            rows.append(item.model_dump(mode="json"))
            bindings.append(
                {
                    "case_id": item_id,
                    "workflow_case_id": case["case_id"],
                    "turn": turn["turn"],
                    "execution_id": case["execution_id"],
                    "answer_sha256": hashlib.sha256(result["reply"].encode()).hexdigest(),
                    "authorized_evidence_records": evidence_records,
                }
            )
    return rows, bindings, skipped


async def run(
    output,
    mode="offline",
    live_config=None,
    on_snapshot=None,
    *,
    answer_prompt_variant: AnswerPromptVariant = "baseline",
):
    if output.exists():
        raise FileExistsError(output)
    prompt = capture_answer_prompt(answer_prompt_variant)
    cases = [c for p in DATASETS for c in load_workflow_cases(p)]
    if len({c.case_id for c in cases}) != len(cases):
        raise ValueError("duplicate cross-dataset case ID")
    source_before = source_manifest()
    datasets_before = input_manifest()
    corpus_before = corpus_hash()
    provider, budget = make_provider(mode, live_config, answer_prompt_variant)
    client = BudgetedCaptureClient(provider, budget, answer_prompt_variant=answer_prompt_variant)
    output.mkdir(parents=True)

    def partial(captures, evaluations):
        (output / "capture-partial.json").write_text(
            json.dumps(
                {
                    "results": captures,
                    "workflow_results": evaluations,
                    "provider_records": client.records,
                    "provider_receipts": getattr(provider, "receipts", []),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            "utf-8",
        )
        if on_snapshot is not None:
            on_snapshot(captures, evaluations)

    try:
        with tempfile.TemporaryDirectory(prefix="workflow-capture-") as directory:
            report = await capture_workflow_cases(
                cases, client, directory, ROOT / "data/policies", partial
            )
    finally:
        if mode == "live":
            await provider.close()
    report["manifest"] = {
        "datasets": datasets_before,
        "corpus_sha256": corpus_before,
        "source_sha256": source_before,
        "answer_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
    }
    report["answer_prompt_variant"] = answer_prompt_variant
    report["source_or_input_drift"] = (
        any(sha(ROOT / p) != digest for p, digest in source_before.items())
        or input_manifest() != datasets_before
        or corpus_hash() != corpus_before
    )
    report["provider_receipts"] = getattr(provider, "receipts", [])
    report["live_provider_calls"] = client.calls if mode == "live" else 0
    report["model_transport_errors"] = sum(r.get("status") != "ok" for r in client.records)
    semantic, bindings, skipped = export_semantic(report["results"])
    passed = sum(r["passed"] for r in report["workflow_results"])
    summary = {
        "mode": mode,
        "cases": len(cases),
        "turns": sum(len(c.turns) for c in cases),
        "workflow_passed": passed,
        "workflow_gate_passed": passed == len(cases)
        and not report["source_or_input_drift"]
        and report["model_transport_errors"] == 0,
        "provider_calls": client.calls,
        "live_provider_calls": report["live_provider_calls"],
        "semantic_faithfulness_inputs": len(semantic),
        "semantic_skipped_turns": len(skipped),
        "human_reviewed_samples": 0,
        "model_quality_certified": False,
    }
    for name, value in {
        "capture-report.json": report,
        "summary.json": summary,
        "semantic-bindings.json": bindings,
        "semantic-skipped.json": skipped,
    }.items():
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (output / "semantic-inputs.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in semantic), "utf-8"
    )
    write_pending_cards(report["workflow_results"], output / "pending-review.jsonl")
    (output / "summary.md").write_text(
        f"# Agent workflow capture\n\nMode: {mode}; workflow checks: {passed}/{len(cases)}; captured turns: {summary['turns']}.\n\n"
        f"Faithfulness inputs: {len(semantic)}. Human reviews: 0. Model quality not certified.\n",
        "utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--live-config", type=Path)
    parser.add_argument(
        "--answer-prompt-variant", choices=["baseline", "completeness-v1"], default="baseline"
    )
    args = parser.parse_args()
    summary = asyncio.run(
        run(
            args.output_dir,
            args.mode,
            args.live_config,
            answer_prompt_variant=args.answer_prompt_variant,
        )
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["workflow_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
