"""Local scripted-provider capture through actual Agent components; no live CLI yet."""

import argparse
import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

from app.evaluation.agent_capture import BudgetedCaptureClient, capture_cases, load_capture_cases
from app.evaluation.judge import JudgeBudget

ROOT = Path(__file__).resolve().parents[1]


class ScriptedCaptureProvider:
    model_version = "scripted-capture-v1"
    evidence_mode = "offline_scripted"

    async def complete(self, messages, max_output_tokens):
        if "意图分类器" in messages[0]["content"]:
            payload = json.loads(messages[-1]["content"].split("\n", 1)[1])
            intent = "draft_generation" if "草稿" in payload["request"] else "policy_query"
            return json.dumps(
                {"intent": intent, "confidence": 1.0, "reason": "scripted fixture"}
            ), None
        return "合成制度要求提交申请。[S1]", None


async def run(dataset, output):
    if output.exists():
        raise FileExistsError(output)
    dataset.relative_to(ROOT)
    cases = load_capture_cases(dataset)
    client = BudgetedCaptureClient(
        ScriptedCaptureProvider(),
        JudgeBudget(
            token_limit=100000,
            cost_limit=1,
            max_output_tokens=1000,
            max_usd_per_million=1,
            timeout_seconds=5,
            max_calls=20,
        ),
    )
    with tempfile.TemporaryDirectory(prefix="agent-capture-") as directory:
        report = await capture_cases(cases, client, directory, ROOT / "data/policies")
    paths = [
        dataset,
        ROOT / "app/evaluation/agent_capture.py",
        Path(__file__),
        ROOT / "app/agent/intent_classifier.py",
        ROOT / "app/rag/policy_answer_service.py",
        ROOT / "app/agent/workflow.py",
        ROOT / "app/evaluation/workflow_fixtures.py",
    ]
    paths.extend(sorted((ROOT / "data/policies").rglob("*.md")))
    paths.extend(sorted((ROOT / "app").rglob("*.py")))
    report["manifest"] = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }
    report["data_scope"] = (
        "synthetic principal and synthetic retrieval fixtures; local isolated simulated approvals"
    )
    report["live_provider_calls"] = 0
    report["principal"] = {
        "employee_id": "EVAL-001",
        "department": "Evaluation",
        "roles": ["EMPLOYEE"],
        "clearance": "internal",
        "identity_source": "test_fixture",
    }
    output.mkdir(parents=True)
    (output / "capture-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    print(
        json.dumps(
            {
                "cases": len(cases),
                "turns": sum(len(c.queries) for c in cases),
                "scripted_calls": client.calls,
                "live_calls": 0,
            }
        )
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "tests/evaluation/agent_capture_cases.jsonl"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.dataset.resolve(), args.output_dir))


if __name__ == "__main__":
    main()
