import json
from pathlib import Path

import pytest

from app.evaluation.failure_pipeline import approve_regression, failure_cards
from app.evaluation.judge import (
    JudgeBudget,
    JudgeInput,
    MockJudgeProvider,
    SemanticJudge,
    calibration,
)
from app.evaluation.pairwise import compare_pair, summarize_pairs
from app.evaluation.workflow_evaluation import load_workflow_cases, run_sqlite_cases

ROOT = Path(__file__).resolve().parents[2]


def judge(response, **changes):
    values = dict(
        token_limit=100000,
        cost_limit=1,
        max_output_tokens=200,
        max_usd_per_million=1,
        timeout_seconds=1,
        max_calls=10,
    )
    values.update(changes)
    return SemanticJudge(MockJudgeProvider(response), JudgeBudget(**values))


def item():
    return JudgeInput(
        case_id="J-1", rubric="faithfulness", query="q", answer="a", evidence={"e1": "evidence"}
    )


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{}",
        json.dumps({"verdict": "PASS", "rationale": "ok", "evidence_ids": ["invented"]}),
    ],
)
async def test_judge_invalid_never_passes(raw):
    result = await judge(raw).evaluate(item())
    assert result.status == "invalid_output"
    assert result.decision.verdict == "UNKNOWN"


async def test_budget_prevents_provider_call():
    j = judge("{}", token_limit=1)
    result = await j.evaluate(item())
    assert result.status == "budget_exhausted"
    assert j.calls == 0


async def test_provider_failure_explicit():
    class Broken(MockJudgeProvider):
        async def complete(self, *args):
            raise RuntimeError("private-provider-error")

    j = judge("{}")
    j.provider = Broken("{}")
    result = await j.evaluate(item())
    assert result.status == "provider_error"
    assert "private-provider-error" not in result.model_dump_json()


def test_calibration_excludes_pending_and_unknown():
    assert calibration([{"human": "PASS", "judge": "PASS"}])["n"] == 0
    rows = [
        dict(human=h, judge=j, reviewer="fixture", review_status="human_reviewed")
        for h, j in [("PASS", "PASS"), ("PASS", "FAIL"), ("FAIL", "PASS"), ("FAIL", "FAIL")]
    ]
    report = calibration(rows)
    assert report["agreement"] == 0.5 and report["kappa"] == 0
    assert report["false_pass_rate"] == report["false_fail_rate"] == 0.5


def candidate(safe=True):
    return {
        "answer": "synthetic",
        "manifest": {
            k: "fixed"
            for k in ("dataset_sha256", "principal_sha256", "corpus_sha256", "tool_fixture_sha256")
        },
        "deterministic": {"safety_passed": safe, "final_state_passed": True},
        "workflow_result": {
            "case_id": "p1",
            "assertions": [
                {"name": "turn_0." + name, "expected": value, "actual": value, "passed": safe}
                for name, value in (
                    ("error", None),
                    ("side_effect_count", 0),
                    ("duplicate_side_effect", True),
                    ("persisted_phase", "idle"),
                )
            ],
        },
    }


async def test_pairwise_position_bias_and_safety():
    j = judge(
        json.dumps(dict(verdict="PASS", rationale="evidence", evidence_ids=["e1"], preference="A"))
    )
    kwargs = dict(
        case_id="p1",
        query="q",
        evidence={"e1": "e"},
        candidate_a=candidate(),
        candidate_b=candidate(),
    )
    row = await compare_pair(j, **kwargs)
    assert row["winner"] == "Unknown" and row["order_consistent"] is False
    kwargs["candidate_a"] = candidate(False)
    blocked = await compare_pair(j, **kwargs)
    assert blocked["status"] == "ineligible" and blocked["judge_calls"] == 0
    kwargs["candidate_a"]["manifest"]["corpus_sha256"] = "different"
    with pytest.raises(ValueError, match="identity mismatch"):
        await compare_pair(j, **kwargs)


def test_pair_bootstrap_reproducible_and_small_sample_unknown():
    rows = [
        dict(
            case_id=str(i),
            winner="A" if i % 2 else "B",
            order_consistent=True,
            judge_calls=2,
            judge_failures=0,
        )
        for i in range(20)
    ]
    assert summarize_pairs(rows) == summarize_pairs(rows)
    assert summarize_pairs(rows)["a_win_rate_ci95"] is not None
    assert summarize_pairs(rows[:2])["a_win_rate_ci95"] is None


def test_failure_intake_requires_review_and_excludes_holdout():
    source = dict(
        case_id="x",
        split="holdout",
        passed=False,
        assertions=[dict(name="turn_0.error", passed=False)],
    )
    card = failure_cards([source])[0]
    assert card["reviewer_decision"] is None
    with pytest.raises(ValueError, match="holdout"):
        approve_regression(card, reviewer="human", decision="approve", rationale="checked")


@pytest.mark.parametrize(
    "dataset",
    ["workflow_cases.jsonl", "workflow_challenge_cases.jsonl", "workflow_safety_cases.jsonl"],
)
async def test_dataset_runs_actual_persisted_workflow(tmp_path, dataset):
    cases = load_workflow_cases(ROOT / "tests/evaluation" / dataset)
    results = await run_sqlite_cases(cases, tmp_path, ROOT / "data/policies")
    failures = [(r["case_id"], a) for r in results for a in r["assertions"] if not a["passed"]]
    assert not failures


def test_dataset_rejects_duplicate_and_unknown_fields(tmp_path):
    path = tmp_path / "cases.jsonl"
    row = (
        (ROOT / "tests/evaluation/workflow_cases.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    path.write_text(row + "\n" + row, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_workflow_cases(path)
    invalid = json.loads(row)
    invalid["turns"][0]["fields"] = {"misspelled_amount": None}
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError):
        load_workflow_cases(path)


async def test_valid_semantic_judge_and_call_cap():
    raw = json.dumps(dict(verdict="PASS", rationale="supported by e1", evidence_ids=["e1"]))
    j = judge(raw, max_calls=1)
    assert (await j.evaluate(item())).decision.verdict == "PASS"
    assert (await j.evaluate(item())).status == "budget_exhausted"
    assert j.calls == 1


async def test_timeout_retains_budget():
    import asyncio

    class Slow(MockJudgeProvider):
        async def complete(self, *args):
            await asyncio.sleep(1)

    j = judge("{}", timeout_seconds=0.01)
    j.provider = Slow("{}")
    assert (await j.evaluate(item())).status == "provider_error"
    assert j.reserved_tokens > 0


async def test_cost_budget_prevents_any_call():
    j = judge("{}", cost_limit=0.000000001)
    assert (await j.evaluate(item())).status == "budget_exhausted"
    assert j.calls == 0


async def test_fluent_output_cannot_override_wrong_database_expectation(tmp_path):
    cases = load_workflow_cases(ROOT / "tests/evaluation/workflow_cases.jsonl")
    case = cases[0]
    bad_turn = case.turns[-1].model_copy(update={"submissions": 2})
    bad_case = case.model_copy(update={"turns": (*case.turns[:-1], bad_turn)})
    result = (await run_sqlite_cases([bad_case], tmp_path, ROOT / "data/policies"))[0]
    assert not result["passed"]
    assert any(
        a["name"].endswith("side_effect_count") and not a["passed"] for a in result["assertions"]
    )


async def test_pairwise_is_blind_and_maps_swapped_order():
    class ContentPreference(MockJudgeProvider):
        async def complete(self, messages, max_output_tokens):
            assert "private-model-name" not in json.dumps(messages)
            data = json.loads(messages[-1]["content"])
            return json.dumps(
                dict(
                    verdict="PASS",
                    rationale="supported",
                    evidence_ids=["e1"],
                    preference="A" if data["answer"] == "better" else "B",
                )
            ), None

    j = judge("{}")
    j.provider = ContentPreference("{}")
    a, b = candidate(), candidate()
    a["answer"] = "better"
    a["model"] = "private-model-name"
    row = await compare_pair(
        j, case_id="p1", query="q", evidence={"e1": "e"}, candidate_a=a, candidate_b=b
    )
    assert row["winner"] == "A" and row["order_consistent"] is True


def test_sanitized_experiment_export_and_failure_isolation():
    from app.evaluation.experiment_tracking import publish_workflow_experiment

    report = dict(
        manifest={"model": "private-model"},
        results=[dict(case_id="employee-id", passed=True, query="private-policy")],
    )

    class Client:
        def run_experiment(self, **kwargs):
            payload = json.dumps(dict(data=kwargs["data"], metadata=kwargs["metadata"]))
            assert all(x not in payload for x in ["employee-id", "private-policy", "private-model"])
            output = kwargs["task"](item=kwargs["data"][0])
            assert output is True

        def flush(self):
            raise RuntimeError("transport unavailable")

    assert publish_workflow_experiment(report, Client())["status"] == "export_failed"
    assert report["results"][0]["passed"] is True


def test_regression_comparison_rejects_dataset_drift():
    from app.evaluation.failure_pipeline import regression_summary

    before = dict(manifest={"dataset_sha256": "same"}, results=[dict(case_id="x", passed=True)])
    after = dict(manifest={"dataset_sha256": "same"}, results=[dict(case_id="x", passed=False)])
    assert regression_summary(before, after)["regressions"] == ["x"]
    after["manifest"]["dataset_sha256"] = "changed"
    with pytest.raises(ValueError, match="identical dataset"):
        regression_summary(before, after)


def test_live_workflow_is_manual_and_budgets_explicit():
    import yaml

    config = yaml.load(
        (ROOT / ".github/workflows/live-evaluation.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(config["on"]) == {"workflow_dispatch"}
    inputs = config["on"]["workflow_dispatch"]["inputs"]
    assert {
        "model",
        "token_budget",
        "cost_budget",
        "max_usd_per_million",
        "repetitions",
    } <= inputs.keys()
    step = next(
        s for s in config["jobs"]["live-judge"]["steps"] if "EVAL_API_KEY" in s.get("env", {})
    )
    assert "${{ inputs." not in step["run"]
    assert "--max-calls 20" in step["run"] and "--timeout 30" in step["run"]
