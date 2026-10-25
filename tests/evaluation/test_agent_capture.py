import asyncio
import json
from pathlib import Path

import pytest

from app.evaluation.agent_capture import (
    BudgetedCaptureClient,
    CaptureBudgetExceeded,
    capture_cases,
    load_capture_cases,
)
from app.evaluation.judge import JudgeBudget
from app.evaluation.workflow_fixtures import FORBIDDEN
from scripts.capture_agent_pilot import ScriptedCaptureProvider

ROOT = Path(__file__).resolve().parents[2]
MESSAGES = [{"role": "user", "content": "synthetic"}]


def client(provider=None, **changes):
    budget = dict(
        token_limit=100000,
        cost_limit=1,
        max_output_tokens=100,
        max_usd_per_million=1,
        timeout_seconds=1,
        max_calls=10,
    )
    budget.update(changes)
    return BudgetedCaptureClient(provider or ScriptedCaptureProvider(), JudgeBudget(**budget))


async def test_actual_router_capture_acl_blocking_and_persisted_replay(tmp_path):
    cases = load_capture_cases(ROOT / "tests/evaluation/agent_capture_cases.jsonl")
    result = await capture_cases(cases, client(), tmp_path, ROOT / "data/policies")
    assert result["provider_mode"] == "offline_scripted"
    assert result["quality_conclusion"] is None and result["provider_calls"] == 4
    allowed, denied, injected, purchase = result["results"]
    assert len({c["execution_id"] for c in result["results"]}) == 4
    turn = allowed["turns"][0]
    assert turn["result"]["citations"][0]["source_id"] == "S1"
    assert turn["retrieved_chunk_ids"] == ["fixture-allowed"]
    assert FORBIDDEN not in json.dumps(turn["provider_records"], ensure_ascii=False)
    assert len(denied["turns"][0]["provider_records"]) == 1
    assert denied["turns"][0]["result"]["citations"] == []
    assert denied["turns"][0]["retrieved_chunk_ids"] == []
    assert injected["turns"][0]["error_type"] == "PromptInjectionBlockedError"
    assert injected["turns"][0]["provider_records"] == []
    assert injected["turns"][0]["persisted_session"] is None
    assert [t["persisted_session"]["phase"] for t in purchase["turns"]] == [
        "awaiting_confirmation",
        "confirmed",
        "submitted",
        "submitted",
    ]
    first, replay = purchase["turns"][-2:]
    assert first["persisted_submission"] == replay["persisted_submission"]
    assert replay["result"]["submission"]["duplicate_submission"] is True
    assert all(t["query"] and t["result"]["workflow"]["steps"] for t in purchase["turns"])


@pytest.mark.parametrize(
    "limits", [{"token_limit": 1}, {"cost_limit": 0.000000001}, {"max_calls": 1}]
)
async def test_budget_prevents_extra_provider_calls(limits):
    c = client(**limits)
    if limits.get("max_calls") == 1:
        await c.chat(MESSAGES)
    with pytest.raises(CaptureBudgetExceeded):
        await c.chat(MESSAGES)
    assert c.calls == (1 if "max_calls" in limits else 0)
    assert c.records[-1]["provider_called"] is False


async def test_concurrent_calls_share_budget():
    c = client(max_calls=1)
    results = await asyncio.gather(c.chat(MESSAGES), c.chat(MESSAGES), return_exceptions=True)
    assert sum(isinstance(r, CaptureBudgetExceeded) for r in results) == 1
    assert c.calls == 1


@pytest.mark.parametrize("kind", ["failure", "timeout"])
async def test_failure_keeps_reservation_and_does_not_retry(kind):
    class Broken:
        async def complete(self, *args):
            if kind == "timeout":
                await asyncio.sleep(10)
            raise RuntimeError("private-provider-error")

    c = client(Broken(), max_calls=1, timeout_seconds=0.01)
    with pytest.raises((RuntimeError, TimeoutError)):
        await c.chat(MESSAGES)
    assert c.reserved_tokens > 0 and c.calls == 1
    assert "private-provider-error" not in json.dumps(c.records)
    with pytest.raises(CaptureBudgetExceeded):
        await c.chat(MESSAGES)


@pytest.mark.parametrize("tokens", [-1, True, 10000000])
async def test_invalid_usage_exhausts_remaining_budget(tokens):
    class Invalid:
        async def complete(self, *args):
            return "text", tokens

    c = client(Invalid())
    with pytest.raises(ValueError, match="usage"):
        await c.chat(MESSAGES)
    with pytest.raises(CaptureBudgetExceeded):
        await c.chat(MESSAGES)
    assert c.calls == 1


async def test_cancellation_is_recorded_and_propagated():
    started = asyncio.Event()

    class Waiting:
        async def complete(self, *args):
            started.set()
            await asyncio.Event().wait()

    c = client(Waiting())
    task = asyncio.create_task(c.chat(MESSAGES))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert c.records[0]["status"] == "cancelled" and c.reserved_tokens > 0


async def test_budget_failure_still_captures_router_state(tmp_path):
    cases = load_capture_cases(ROOT / "tests/evaluation/agent_capture_cases.jsonl")[:1]
    c = client(token_limit=1)
    result = await capture_cases(cases, c, tmp_path, ROOT / "data/policies")
    turn = result["results"][0]["turns"][0]
    assert c.calls == 0
    assert turn["result"]["status"] == "unavailable"
    assert turn["persisted_session"] is not None
    assert turn["provider_records"][0]["status"] == "budget_exhausted"
