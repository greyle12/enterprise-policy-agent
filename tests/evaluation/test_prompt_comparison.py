import copy
import hashlib
import json

import pytest

from app.evaluation.agent_capture import (
    BudgetedCaptureClient,
    capture_answer_prompt,
)
from app.evaluation.judge import JudgeBudget
from app.rag.policy_answer_service import _SYSTEM_PROMPT
from scripts import run_agent_workflow_capture as pipeline
from scripts.run_prompt_comparison import run


async def test_candidate_changes_only_answer_system_prompt_and_keeps_caller_messages():
    sent = []

    class Provider:
        async def complete(self, messages, max_output_tokens):
            sent.append(messages)
            return "synthetic [S1]", None

    client = BudgetedCaptureClient(
        Provider(),
        JudgeBudget(
            token_limit=100000,
            cost_limit=1,
            max_output_tokens=1000,
            max_usd_per_million=1,
            timeout_seconds=1,
            max_calls=3,
        ),
        answer_prompt_variant="completeness-v1",
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "untrusted synthetic evidence"},
    ]
    original = copy.deepcopy(messages)
    await client.chat(messages)
    assert messages == original
    assert sent[0][0]["content"] == capture_answer_prompt("completeness-v1")
    assert sent[0][0]["content"].startswith(_SYSTEM_PROMPT)
    assert sent[0][1] == messages[1]
    classifier = [{"role": "system", "content": "意图分类器"}, messages[1]]
    await client.chat(classifier)
    assert sent[1] == classifier
    assert capture_answer_prompt() == _SYSTEM_PROMPT
    with pytest.raises(ValueError, match="unknown capture"):
        capture_answer_prompt("untrusted")


async def test_independent_offline_prompt_experiment_is_bound_and_never_live(tmp_path, monkeypatch):
    import os
    from app.observability import tracing

    monkeypatch.setenv("AGENT_TRACING_ENABLED", "true")
    monkeypatch.setattr(tracing, "LangfuseBackend", lambda: pytest.fail("telemetry export"))
    monkeypatch.setattr(
        pipeline, "DeepSeekCaptureProvider", lambda **kw: pytest.fail("live provider")
    )
    output = tmp_path / "experiment"
    summary = await run(output)
    assert os.environ["AGENT_TRACING_ENABLED"] == "true"
    assert summary["engineering_gate_passed"] and summary["paired_answers"] == 3
    assert summary["mock_judge_calls"] == 6 and summary["live_model_calls"] == 0
    assert summary["quality_winner"] is None and not summary["real_ab_input_ready"]
    reports = [
        json.loads((output / v / "capture-report.json").read_text("utf-8"))
        for v in ("baseline", "completeness-v1")
    ]
    assert reports[0]["manifest"]["source_sha256"] == reports[1]["manifest"]["source_sha256"]
    assert (
        reports[0]["manifest"]["answer_prompt_sha256"]
        != reports[1]["manifest"]["answer_prompt_sha256"]
    )
    ids = [{c["execution_id"] for c in r["results"]} for r in reports]
    assert not ids[0] & ids[1]
    for variant, report in zip(("baseline", "completeness-v1"), reports, strict=True):
        assert len(report["workflow_results"]) == 19
        assert all(r["passed"] for r in report["workflow_results"])
        actual = [
            call["messages"][0]["content"]
            for c in report["results"]
            for t in c["turns"]
            for call in t["provider_records"]
            if any("\n<policy_evidence_json>" in m["content"] for m in call["messages"])
        ]
        assert len(actual) == 3 and set(actual) == {capture_answer_prompt(variant)}
        assert all(
            hashlib.sha256(p.encode()).hexdigest() == report["manifest"]["answer_prompt_sha256"]
            for p in actual
        )
        assert (
            hashlib.sha256((output / variant / "answer-system-prompt.txt").read_bytes()).hexdigest()
            == report["manifest"]["answer_prompt_sha256"]
        )
        with pytest.raises(ValueError):
            pipeline.LiveCaptureConfig.model_validate_json(
                (output / variant / "live-config.pending.json").read_text("utf-8")
            )
    with pytest.raises(FileExistsError):
        await run(output)


def test_live_variant_mismatch_blocks_before_provider_construction(tmp_path, monkeypatch):
    config = {
        "model": "fixture",
        "base_url": "https://api.deepseek.com",
        "budget": {
            "token_limit": 10000,
            "cost_limit": 1,
            "max_output_tokens": 100,
            "max_usd_per_million": 1,
            "timeout_seconds": 1,
            "max_calls": 1,
        },
        "dataset_sha256": pipeline.input_manifest(),
        "corpus_sha256": pipeline.corpus_hash(),
        "source_sha256": pipeline.source_manifest(),
        "approved_data_scope": "workflow_development_and_synthetic_fixtures",
        "answer_prompt_variant": "baseline",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), "utf-8")
    monkeypatch.setattr(
        pipeline, "DeepSeekCaptureProvider", lambda **kw: pytest.fail("provider constructed")
    )
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        pipeline.make_provider("live", path, "completeness-v1")
